from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import pytest
from cpipe.delivery.journal import Journal
from cpipe.delivery import service
from .test_execution_plan import plan
from .test_service import event, receipt
from cpipe.delivery.contracts import Outcome


def proposal():
    p = plan()
    return service.Event('start', 'proposal_created', 'd1', p.digest(), 'tree1', 0, 100,
                         plan=p, session_id=p.envelope.session_id, profile_id=p.envelope.profile_id)


def controller(journal):
    assert hasattr(service, 'Service'), 'durable Service is missing'
    return service.Service(journal)


def test_restart_replays_original_decision_without_rolling_back_current_state(tmp_path):
    path = tmp_path / 'facts.db'
    fact = proposal()
    with Journal(path) as journal:
        api = controller(journal)
        original = api.accept(fact)
        state, intents = original
        approved, _ = api.accept(event(state, 'envelope_approved'))
        assert api.read('d1') == approved
    with Journal(path) as journal:
        api = controller(journal)
        assert api.accept(fact) == original
        assert api.read('d1') == approved
        with pytest.raises(ValueError, match='idempotency'):
            api.accept(replace(fact, artifact='different'))
        assert api.pending() == [(approved.active_intent, journal.pending()[-1][1])]


def test_cancel_and_claim_fence_old_outbox_intents(tmp_path):
    with Journal(tmp_path / 'facts.db') as journal:
        api = controller(journal)
        state, _ = api.accept(proposal())
        state, _ = api.accept(event(state, 'envelope_approved'))
        assert len(api.pending()) == 1
        state, _ = api.accept(event(state, 'activity_claimed', run_id='native1'))
        assert api.pending() == []
        state, _ = api.accept(event(state, 'cancelled'))
        assert api.pending() == []
        assert journal.pending()  # tombstones are retained, not deleted
        assert api.read('d1').phase == 'cancelled'


def test_failed_transaction_rolls_back_projection_and_event(tmp_path):
    with Journal(tmp_path / 'facts.db') as journal:
        api = controller(journal)
        journal.db.execute("CREATE TEMP TRIGGER crash BEFORE INSERT ON intent BEGIN SELECT RAISE(ABORT, 'crash'); END")
        with pytest.raises(Exception, match='crash'):
            api.accept(proposal())
        assert journal.read('d1') == (0, None)
        assert journal.pending() == []
        journal.db.execute('DROP TRIGGER crash')
        assert api.accept(proposal())[0].version == 1


def test_lost_commit_response_reconciles_to_one_decision(tmp_path, monkeypatch):
    path = tmp_path / 'facts.db'
    with Journal(path) as journal:
        api = controller(journal)
        original = journal.append
        def lose_response(*args, **kwargs):
            original(*args, **kwargs)
            raise ConnectionError('lost response after commit')
        monkeypatch.setattr(journal, 'append', lose_response)
        with pytest.raises(ConnectionError):
            api.accept(proposal())
    with Journal(path) as journal:
        api = controller(journal)
        assert api.accept(proposal())[0].version == 1
        assert len(api.pending()) == 1
        assert journal.db.execute('SELECT count(*) FROM event').fetchone()[0] == 1


def test_concurrent_duplicate_accepts_one_event(tmp_path):
    path = tmp_path / 'facts.db'
    with Journal(path):
        pass
    def accept():
        with Journal(path) as journal:
            return controller(journal).accept(proposal())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: accept(), range(2)))
    assert results[0] == results[1]
    with Journal(path) as journal:
        assert journal.read('d1')[0] == 1
        assert len(journal.pending()) == 1


def test_conflicting_event_cannot_overwrite_newer_state(tmp_path):
    with Journal(tmp_path / 'facts.db') as journal:
        api = controller(journal)
        state, _ = api.accept(proposal())
        first = event(state, 'envelope_approved')
        approved, _ = api.accept(first)
        with pytest.raises((ValueError, RuntimeError), match='stale|version'):
            api.accept(replace(first, key='different'))
        assert api.read('d1') == approved


def test_restart_keeps_feedback_and_same_builder_without_duplicate_intent(tmp_path):
    path = tmp_path / 'facts.db'
    with Journal(path) as journal:
        api = controller(journal)
        state, _ = api.accept(proposal())
        state, _ = api.accept(event(state, 'envelope_approved'))
        state, _ = api.accept(event(state, 'activity_claimed', run_id='tests1'))
        state, _ = api.accept(event(state, 'activity_observed',
                                    receipts=(receipt(state, outcome=Outcome.FAIL),)))
        state, _ = api.accept(event(state, 'activity_claimed', run_id='build1',
                                    actor_id='forge', worker_session='builder-session'))
        state, _ = api.accept(event(state, 'activity_observed', actor_id='forge',
                                    result_artifact='tree2'))
        state, _ = api.accept(event(state, 'activity_claimed', run_id='checks1'))
        state, _ = api.accept(event(state, 'activity_observed', receipts=(receipt(state),)))
        fact = event(state, 'feedback_received', action='change',
                     evidence_ref='feedback/label.json')
        original = api.accept(fact)
        count = journal.db.execute('SELECT count(*) FROM intent').fetchone()[0]
    with Journal(path) as journal:
        api = controller(journal)
        assert api.accept(fact) == original
        assert api.read('d1') == original[0]
        assert api.pending() == list(original[1])
        assert api.pending()[0][1]['feedback_ref'] == 'feedback/label.json'
        assert api.pending()[0][1]['resume'] == 'builder-session'
        assert journal.db.execute('SELECT count(*) FROM intent').fetchone()[0] == count
        with pytest.raises(ValueError, match='idempotency'):
            api.accept(replace(fact, evidence_ref='feedback/other.json'))
