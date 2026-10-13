"""Native activity observations; subprocess fixtures are not live-model proof."""
from dataclasses import replace
import sys

import pytest

from cpipe.delivery import native_bridge
from cpipe.delivery.artifact import fingerprint
from cpipe.delivery.contracts import Outcome
from cpipe.runtime import hermes_api as h
from .test_artifact import repo
from .test_check_runner import setup_check


def implementing(repo, tmp_path, board):
    journal, service, state, spec = setup_check(repo, tmp_path, write_paths=('double.py',))
    b = native_bridge.NativeBridge(service, board, session_id='s1', profile_id='default')
    tid = b.schedule('d1')
    h.claim_task(board, tid, claimer='controller', ttl_seconds=60)
    b.bind_claim('d1', tid, claimer='controller', worker_session='checks')
    b.observe_checks('d1', tid, claimer='controller', checks=(spec,),
                     evidence_root=tmp_path / 'evidence')
    return journal, service, b, b.schedule('d1')


def test_observed_builder_edits_advance_to_checks_not_ready(repo, tmp_path, board):
    from cpipe.delivery import workers
    journal, service, b, tid = implementing(repo, tmp_path, board)
    with journal:
        def builder(state, *, on_session, heartbeat, **kwargs):
            on_session('builder-session')
            heartbeat()
            (repo / 'double.py').write_text('def double(n): return n * 2\n')
            return workers.WorkerResult('builder-session', {'status': 'done'})
        after = workers.observe_implementation(b, 'd1', tid, claimer='controller', execute=builder)
        assert after.activity == 'checks' and after.phase == 'checking'
        assert after.artifact == fingerprint(repo).digest
        assert after.builder_session == 'builder-session'
        assert h.get_task(board, tid).status == 'done'
        assert len(after.receipts) == 1 and after.receipts[0].outcome is Outcome.FAIL


def test_worker_claim_of_done_without_edit_is_not_implementation(repo, tmp_path, board):
    from cpipe.delivery import workers
    journal, service, b, tid = implementing(repo, tmp_path, board)
    with journal:
        def builder(state, *, on_session, **kwargs):
            on_session('builder-session')
            return workers.WorkerResult('builder-session', {'status': 'done'})
        with pytest.raises(ValueError, match='successor artifact'):
            workers.observe_implementation(b, 'd1', tid, claimer='controller', execute=builder)
        assert service.read('d1').activity == 'implement'
        assert h.get_task(board, tid).status != 'done'


@pytest.mark.parametrize('outside,allowed', [('unauthorized.py', True), ('test_behavior.py', True),
                                             ('internal/report.txt', False)])
def test_only_forbidden_paths_stop_a_builder_write_paths_are_guidance(repo, tmp_path, board, outside, allowed):
    from cpipe.delivery import workers
    journal, service, b, tid = implementing(repo, tmp_path, board)
    with journal:
        def builder(state, *, on_session, **kwargs):
            on_session('builder-session')
            target = repo / outside
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('not approved')
            return workers.WorkerResult('builder-session', {'status': 'done'})
        if allowed:
            workers.observe_implementation(b, 'd1', tid, claimer='controller', execute=builder)
            assert h.get_task(board, tid).status == 'done'
            return
        with pytest.raises(ValueError, match='forbidden path'):
            workers.observe_implementation(b, 'd1', tid, claimer='controller', execute=builder)
        assert h.get_task(board, tid).status != 'done'


def test_a_builder_that_commits_keeps_its_edits_uncommitted(repo, tmp_path, board):
    from cpipe.delivery import workers
    from .test_artifact import git
    journal, service, b, tid = implementing(repo, tmp_path, board)
    base = git(repo, 'rev-parse', 'HEAD')
    with journal:
        def builder(state, *, on_session, **kwargs):
            on_session('builder-session')
            (repo / 'double.py').write_text('def double(n): return n * 2\n')
            git(repo, 'commit', '-qam', 'fix')
            return workers.WorkerResult('builder-session', {'status': 'done'})
        assert workers.observe_implementation(b, 'd1', tid, claimer='controller', execute=builder).activity == 'checks'
    assert git(repo, 'rev-parse', 'HEAD') == base
    assert (repo / 'double.py').read_text() == 'def double(n): return n * 2\n'


def test_claim_lost_during_worker_does_not_advance_engineering_state(repo, tmp_path, board):
    from cpipe.delivery import workers
    journal, service, b, tid = implementing(repo, tmp_path, board)
    with journal:
        def builder(state, *, on_session, **kwargs):
            on_session('builder-session')
            task = h.get_task(board, tid)
            h.block(board, tid, run_id=task.current_run_id, reason='lost', kind='transient')
            (repo / 'double.py').write_text('def double(n): return n * 2\n')
            return workers.WorkerResult('builder-session', {'status': 'done'})
        with pytest.raises(ValueError, match='ownership'):
            workers.observe_implementation(b, 'd1', tid, claimer='controller', execute=builder)
        assert service.read('d1').activity == 'implement'


def test_unknown_worker_result_and_wrong_session_are_rejected(repo, tmp_path, board):
    from cpipe.delivery import workers
    journal, service, b, tid = implementing(repo, tmp_path, board)
    with journal:
        def builder(state, *, on_session, **kwargs):
            on_session('builder-session')
            (repo / 'double.py').write_text('def double(n): return n * 2\n')
            return workers.WorkerResult('different-session', {'status': 'done'})
        with pytest.raises(ValueError, match='session'):
            workers.observe_implementation(b, 'd1', tid, claimer='controller', execute=builder)
        assert h.get_task(board, tid).status != 'done'
