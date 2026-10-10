"""Real native Kanban claims, not a fabricated worker or live-model proof."""
from dataclasses import replace
import json
import time

import pytest

from cpipe.delivery.contracts import Outcome, Receipt
from cpipe.delivery.journal import Journal
from cpipe.delivery.service import Event, Service
from cpipe.runtime import hermes_api as h
from .test_execution_plan import plan


def approved(tmp_path):
    now = int(time.time())
    p = plan(worktree=str(tmp_path), common_git_dir=str(tmp_path / '.git'),
             expires_at=now + 300)
    journal = Journal(tmp_path / 'bridge.db')
    service = Service(journal)
    state, _ = service.accept(Event('proposal', 'proposal_created', 'd1', p.digest(),
        'tree1', 0, now, session_id='s1', profile_id='default', plan=p))
    return journal, service, state


def approve(service, state):
    return service.accept(Event('approve', 'envelope_approved', 'd1', state.plan.digest(),
        state.artifact, state.version, state.last_at, session_id='s1', profile_id='default',
        activity_id=state.active_intent))[0]


def bridge(service, board, **kw):
    from cpipe.delivery.native_bridge import NativeBridge
    return NativeBridge(service, board, session_id='s1', profile_id='default', **kw)


def behavior(state, outcome=Outcome.PASS):
    c = state.plan.checks[0]
    return Receipt(c.obligation, state.artifact, state.plan.envelope.scope,
                   c.inputs, c.environment, c.command, outcome, True)


def test_no_task_is_scheduled_before_envelope_approval(tmp_path, board):
    journal, service, state = approved(tmp_path)
    with journal:
        assert bridge(service, board).schedule('d1') is None
        assert not h.kb.list_tasks(board)


def test_schedule_is_idempotent_pins_runtime_and_reads_back_subscription(tmp_path, board):
    journal, service, state = approved(tmp_path)
    with journal:
        approve(service, state)
        target = dict(platform='telegram', chat_id='fixture-chat', notifier_profile='default',
                      thread_id=None, user_id=None)
        b = bridge(service, board, notify_target=target)
        tid = b.schedule('d1')
        assert b.schedule('d1') == tid
        task = h.get_task(board, tid)
        assert task.status == 'ready'
        assert task.workspace_kind == 'dir' and task.workspace_path == str(tmp_path)
        assert task.session_id == 's1' and task.created_by == 'cpipe'
        assert task.max_runtime_seconds > 0 and task.idempotency_key
        assert task.model_override == 'model1' and task.provider_override == 'provider1'
        from hermes_cli import kanban_db_notify as notify
        assert notify.list_notify_subs(board, tid)[0]['delivery_mode'] == 'notify+wake'
        assert len(h.kb.list_tasks(board)) == 1
        packet = json.loads(task.body.split('\n', 1)[1])
        assert packet['plan_digest'] == state.plan.digest()
        assert packet['action'] == 'tests'


def test_required_missing_subscription_leaves_no_runnable_task(tmp_path, board):
    journal, service, state = approved(tmp_path)
    with journal:
        approve(service, state)
        with pytest.raises(RuntimeError, match='subscription'):
            bridge(service, board, require_notify=True).schedule('d1')
        assert not h.kb.list_tasks(board)


def test_native_claim_competition_binds_real_run_and_rejects_forged_owner(tmp_path, board):
    journal, service, state = approved(tmp_path)
    with journal:
        approve(service, state)
        b = bridge(service, board)
        tid = b.schedule('d1')
        task = h.claim_task(board, tid, claimer='owner', ttl_seconds=60)
        assert h.claim_task(board, tid, claimer='competitor', ttl_seconds=60) is None
        with pytest.raises(ValueError, match='ownership'):
            b.bind_claim('d1', tid, claimer='competitor', worker_session='worker1')
        bound = b.bind_claim('d1', tid, claimer='owner', worker_session='worker1')
        assert bound.run_id == f'{tid}:{task.current_run_id}'
        assert bound.worker_session == 'worker1'
        assert b.bind_claim('d1', tid, claimer='owner', worker_session='worker1') == bound
        with pytest.raises(ValueError, match='session'):
            b.bind_claim('d1', tid, claimer='owner', worker_session='other')


def test_observed_checks_complete_native_run_and_emit_one_successor(tmp_path, board):
    journal, service, state = approved(tmp_path)
    with journal:
        approve(service, state)
        b = bridge(service, board)
        tid = b.schedule('d1')
        task = h.claim_task(board, tid, claimer='owner', ttl_seconds=60)
        state = b.bind_claim('d1', tid, claimer='owner', worker_session='worker1')
        event = b.result_event('d1', tid, claimer='owner', kind='activity_observed',
                               receipts=(behavior(state, Outcome.FAIL),))
        after = b.finish(event, tid, claimer='owner', summary='Observed behavioral RED')
        assert after.phase == 'implementing'
        assert h.get_task(board, tid).status == 'done'
        run = h.kb.latest_run(board, tid)
        assert run.id == task.current_run_id and run.outcome == 'completed'
        assert run.metadata['cpipe_v2_event'] == event.to_dict()
        assert b.finish(event, tid, claimer='owner', summary='Observed behavioral RED') == after
        next_tid = b.schedule('d1')
        assert next_tid != tid and b.schedule('d1') == next_tid
        next_task = h.get_task(board, next_tid)
        assert next_task.assignee == 'cp:forge'


def test_lost_or_reclaimed_native_run_cannot_advance_journal(tmp_path, board):
    journal, service, state = approved(tmp_path)
    with journal:
        approve(service, state)
        b = bridge(service, board)
        tid = b.schedule('d1')
        task = h.claim_task(board, tid, claimer='owner', ttl_seconds=60)
        state = b.bind_claim('d1', tid, claimer='owner', worker_session='worker1')
        event = b.result_event('d1', tid, claimer='owner', kind='activity_observed',
                               receipts=(behavior(state),))
        h.block(board, tid, run_id=task.current_run_id, reason='ownership ended', kind='transient')
        h.kb.unblock_task(board, tid)
        successor = h.claim_task(board, tid, claimer='new-owner', ttl_seconds=60)
        assert successor.current_run_id != task.current_run_id
        with pytest.raises(ValueError, match='ownership|run'):
            b.finish(event, tid, claimer='owner', summary='late observation')
        assert service.read('d1') == state
        assert h.get_task(board, tid).status == 'running'


def test_cross_session_bridge_cannot_schedule_or_bind(tmp_path, board):
    from cpipe.delivery.native_bridge import NativeBridge
    journal, service, state = approved(tmp_path)
    with journal:
        approve(service, state)
        b = NativeBridge(service, board, session_id='foreign', profile_id='default')
        with pytest.raises(ValueError, match='session|profile'):
            b.schedule('d1')
        assert not h.kb.list_tasks(board)


def test_forged_result_is_rejected_before_native_completion(tmp_path, board):
    journal, service, state = approved(tmp_path)
    with journal:
        approve(service, state)
        b = bridge(service, board)
        tid = b.schedule('d1')
        h.claim_task(board, tid, claimer='owner', ttl_seconds=60)
        state = b.bind_claim('d1', tid, claimer='owner', worker_session='worker1')
        event = b.result_event('d1', tid, claimer='owner', kind='activity_observed',
                               receipts=(behavior(state),))
        with pytest.raises(ValueError):
            b.finish(replace(event, artifact='stale'), tid, claimer='owner', summary='forged')
        assert h.get_task(board, tid).status == 'running'
        assert service.read('d1') == state


def test_native_completion_is_reconciled_after_journal_write_interruption(tmp_path, board, monkeypatch):
    journal, service, state = approved(tmp_path)
    with journal:
        approve(service, state)
        b = bridge(service, board)
        tid = b.schedule('d1')
        h.claim_task(board, tid, claimer='owner', ttl_seconds=60)
        state = b.bind_claim('d1', tid, claimer='owner', worker_session='worker1')
        event = b.result_event('d1', tid, claimer='owner', kind='activity_observed',
                               receipts=(behavior(state),))
        original = service.accept
        def interrupted(value):
            raise RuntimeError('simulated journal interruption')
        with monkeypatch.context() as m:
            m.setattr(service, 'accept', interrupted)
            with pytest.raises(RuntimeError, match='interruption'):
                b.finish(event, tid, claimer='owner', summary='Observed behavior PASS')
        assert h.get_task(board, tid).status == 'done'
        assert service.read('d1') == state
        after = b.finish(event, tid, claimer='owner', summary='Observed behavior PASS')
        assert after.activity == 'checks'
        assert len(h.kb.list_runs(board, tid)) == 1
        assert b.finish(event, tid, claimer='owner', summary='Observed behavior PASS') == after


def test_expired_execution_authority_does_not_queue(tmp_path, board):
    journal, service, state = approved(tmp_path)
    with journal:
        approve(service, state)
        state = service.read('d1')
        from cpipe.delivery import native_bridge
        with pytest.MonkeyPatch.context() as m:
            m.setattr(native_bridge.time, 'time', lambda: state.plan.expires_at + 1)
            with pytest.raises(ValueError, match='expired'):
                bridge(service, board).schedule('d1')
        assert not h.kb.list_tasks(board)


def test_finish_cannot_convert_control_event_into_native_result(tmp_path, board):
    journal, service, state = approved(tmp_path)
    with journal:
        approve(service, state)
        b = bridge(service, board)
        tid = b.schedule('d1')
        h.claim_task(board, tid, claimer='owner', ttl_seconds=60)
        state = b.bind_claim('d1', tid, claimer='owner', worker_session='worker1')
        event = b.result_event('d1', tid, claimer='owner', kind='activity_observed',
                               receipts=(behavior(state),))
        with pytest.raises(ValueError, match='result'):
            b.finish(replace(event, kind='cancelled'), tid, claimer='owner', summary='not a result')
        assert h.get_task(board, tid).status == 'running'
        assert service.read('d1') == state


def test_finish_rejects_wrong_controller_actor(tmp_path, board):
    journal, service, state = approved(tmp_path)
    with journal:
        approve(service, state)
        b = bridge(service, board)
        tid = b.schedule('d1')
        h.claim_task(board, tid, claimer='owner', ttl_seconds=60)
        state = b.bind_claim('d1', tid, claimer='owner', worker_session='worker1')
        event = b.result_event('d1', tid, claimer='owner', kind='activity_observed',
                               receipts=(behavior(state),))
        with pytest.raises(ValueError, match='actor'):
            b.finish(replace(event, actor_id='impostor'), tid, claimer='owner', summary='forged actor')
        assert h.get_task(board, tid).status == 'running'
        assert service.read('d1') == state
