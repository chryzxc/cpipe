"""Real native database + real check subprocess, not a live model delivery."""
import pytest

from cpipe.delivery.native_bridge import NativeBridge
from cpipe.runtime import hermes_api as h
from .test_artifact import repo
from .test_check_runner import setup_check, red_then_implemented


def test_native_observed_red_schedules_a_distinct_builder_activity(repo, tmp_path, board):
    journal, service, state, spec = setup_check(repo, tmp_path)
    with journal:
        b = NativeBridge(service, board, session_id='s1', profile_id='default')
        tid = b.schedule('d1')
        claim = h.claim_task(board, tid, claimer='controller', ttl_seconds=60)
        b.bind_claim('d1', tid, claimer='controller', worker_session='check-worker')
        after = b.observe_checks('d1', tid, claimer='controller', checks=(spec,),
                                 evidence_root=tmp_path / 'evidence')
        assert after.phase == 'implementing'
        assert h.get_task(board, tid).status == 'done'
        native = h.kb.latest_run(board, tid)
        assert native.id == claim.current_run_id and native.outcome == 'completed'
        assert native.metadata['cpipe_v2_event']['receipts'][0]['outcome'] == 'fail'
        next_tid = b.schedule('d1')
        assert next_tid != tid and h.get_task(board, next_tid).status == 'ready'
        assert b.schedule('d1') == next_tid


def test_real_passing_checks_reach_readiness_gate_through_native_results(repo, tmp_path, board):
    journal, service, state, spec = setup_check(repo, tmp_path, write_paths=('double.py',))
    with journal:
        b = NativeBridge(service, board, session_id='s1', profile_id='default')
        red_then_implemented(b, repo, tmp_path, spec)
        tid = b.schedule('d1')
        assert service.read('d1').phase == 'checking'
        h.claim_task(board, tid, claimer='controller', ttl_seconds=60)
        b.bind_claim('d1', tid, claimer='controller', worker_session='check-checks')
        after = b.observe_checks('d1', tid, claimer='controller', checks=(spec,),
                                 evidence_root=tmp_path / 'evidence')
        assert h.get_task(board, tid).status == 'done'
        assert after.phase == 'testable' and not after.activity
        assert b.schedule('d1') is None  # No implicit request for independent review/publication.
        assert len(h.kb.list_tasks(board)) == 3


def test_behavior_test_that_already_passes_gets_one_write_tests_pass(repo, tmp_path, board):
    (repo / 'double.py').write_text('def double(n): return n * 2\n')
    journal, service, state, spec = setup_check(repo, tmp_path)
    with journal:
        b = NativeBridge(service, board, session_id='s1', profile_id='default')
        tid = b.schedule('d1')
        h.claim_task(board, tid, claimer='controller', ttl_seconds=60)
        b.bind_claim('d1', tid, claimer='controller', worker_session='check-green')
        after = b.observe_checks('d1', tid, claimer='controller', checks=(spec,),
                                 evidence_root=tmp_path / 'evidence')
        assert after.activity == 'write_tests' and after.phase == 'implementing'
        assert h.get_task(board, b.schedule('d1')).assignee == 'cp:forge'


def test_lost_native_claim_after_real_check_cannot_apply_observation(repo, tmp_path, board, monkeypatch):
    from cpipe.delivery import check_runner
    journal, service, state, spec = setup_check(repo, tmp_path)
    with journal:
        b = NativeBridge(service, board, session_id='s1', profile_id='default')
        tid = b.schedule('d1')
        claim = h.claim_task(board, tid, claimer='controller', ttl_seconds=60)
        bound = b.bind_claim('d1', tid, claimer='controller', worker_session='check-worker')
        original = check_runner.observe_pytest
        def lose_claim(*a, **kw):
            receipt = original(*a, **kw)
            h.block(board, tid, run_id=claim.current_run_id, reason='lease ended', kind='transient')
            return receipt
        monkeypatch.setattr(check_runner, 'observe_pytest', lose_claim)
        with pytest.raises(ValueError, match='ownership'):
            b.observe_checks('d1', tid, claimer='controller', checks=(spec,),
                             evidence_root=tmp_path / 'evidence')
        assert service.read('d1') == bound
        assert h.get_task(board, tid).status == 'blocked'
