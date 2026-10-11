"""Native independent-review observations; fixtures do not prove a live model run."""
from dataclasses import replace
import json
import time

import pytest

from cpipe.delivery.artifact import fingerprint
from cpipe.delivery.check_runner import PytestCheck
from cpipe.delivery.contracts import Outcome
from cpipe.delivery.journal import Journal
from cpipe.delivery.native_bridge import NativeBridge
from cpipe.delivery.service import Event, Service
from cpipe.delivery.workers import WorkerResult
from cpipe.runtime import hermes_api as h
from .test_artifact import repo, git
from .test_check_runner import red_then_implemented
from .test_execution_plan import plan


def reviewing(repo, tmp_path, board):
    from cpipe.delivery.review import review_identity
    (repo / 'test_behavior.py').write_text('from double import double\ndef test_double(): assert double(2) == 4\n')
    spec = PytestCheck('behavior', ('test_behavior.py',), ('test_behavior::test_double',))
    now = int(time.time())
    p = plan(worktree=str(repo), common_git_dir=str(repo / '.git'),
             base_commit=git(repo, 'rev-parse', 'HEAD'), expires_at=now + 300,
             checks=(spec.identity(), review_identity()), interactive=False,
             write_paths=('double.py',))
    journal = Journal(tmp_path / 'review.db')
    s = Service(journal)
    state, _ = s.accept(Event('proposal', 'proposal_created', 'd1', p.digest(),
        fingerprint(repo).digest, 0, now, session_id='s1', profile_id='default', plan=p))
    state, _ = s.accept(Event('approve', 'envelope_approved', 'd1', p.digest(),
        state.artifact, state.version, now, session_id='s1', profile_id='default',
        activity_id=state.active_intent))
    b = NativeBridge(s, board, session_id='s1', profile_id='default')
    red_then_implemented(b, repo, tmp_path, spec)
    tid = b.schedule('d1')
    h.claim_task(board, tid, claimer='controller', ttl_seconds=60)
    b.bind_claim('d1', tid, claimer='controller', worker_session='checks')
    b.observe_checks('d1', tid, claimer='controller', checks=(spec,),
                     evidence_root=tmp_path / 'evidence')
    assert s.read('d1').activity == 'review'
    return journal, s, b, b.schedule('d1')


def verdict(state, **changes):
    from cpipe.delivery.review import REVIEW_AXES
    value = dict(status='approved', artifact=state.artifact,
                 reviewed_from=state.plan.base_commit,
                 coverage=list(REVIEW_AXES), findings=[], verified_ids=[])
    value.update(changes)
    return value


def reviewer(fn):
    def execute(state, *, on_session, heartbeat, **kwargs):
        on_session('independent-session')
        heartbeat()
        return WorkerResult('independent-session', fn(state))
    return execute


def test_independent_unchanged_full_coverage_review_produces_local_readiness(repo, tmp_path, board):
    from cpipe.delivery.review import observe_review
    journal, s, b, tid = reviewing(repo, tmp_path, board)
    with journal:
        after = observe_review(b, 'd1', tid, claimer='controller',
            execute=reviewer(verdict), evidence_root=tmp_path / 'evidence')
        assert after.phase == 'locally_ready'
        assert after.receipts[-1].obligation == 'review'
        assert after.receipts[-1].outcome is Outcome.PASS
        assert h.get_task(board, tid).status == 'done'


@pytest.mark.parametrize('change', [dict(artifact='stale'), dict(reviewed_from='foreign'),
    dict(coverage=['correctness']), dict(status='unknown'), dict(verified_ids=['REV-999']),
    dict(extra='untrusted')])
def test_stale_incomplete_or_unknown_review_cannot_complete_task(repo, tmp_path, board, change):
    from cpipe.delivery.review import observe_review
    journal, s, b, tid = reviewing(repo, tmp_path, board)
    with journal:
        with pytest.raises(ValueError):
            observe_review(b, 'd1', tid, claimer='controller',
                execute=reviewer(lambda state: verdict(state, **change)),
                evidence_root=tmp_path / 'evidence')
        assert s.read('d1').phase == 'reviewing'
        assert h.get_task(board, tid).status != 'done'


def test_reviewer_mutation_cannot_create_readiness(repo, tmp_path, board):
    from cpipe.delivery.review import observe_review
    journal, s, b, tid = reviewing(repo, tmp_path, board)
    with journal:
        def changed(state):
            (repo / 'double.py').write_text('changed\n')
            return verdict(state)
        with pytest.raises(ValueError, match='artifact changed'):
            observe_review(b, 'd1', tid, claimer='controller', execute=reviewer(changed),
                           evidence_root=tmp_path / 'evidence')
        assert h.get_task(board, tid).status != 'done'


def test_findings_return_to_same_builder_without_authorizing_publication(repo, tmp_path, board):
    from cpipe.delivery.review import observe_review
    journal, s, b, tid = reviewing(repo, tmp_path, board)
    with journal:
        finding = dict(id='', criterion='review', root_cause='negative inputs unhandled',
                       severity='medium', in_scope=True, disposition='open')
        after = observe_review(b, 'd1', tid, claimer='controller',
            execute=reviewer(lambda state: verdict(state, status='changes_requested', findings=[finding])),
            evidence_root=tmp_path / 'evidence')
        assert after.activity == 'implement'
        assert after.findings[0].finding.id == 'REV-001'
        assert after.receipts[-1].outcome is Outcome.FAIL


def test_unrecognized_credential_values_in_reviewer_prose_are_never_retained(repo, tmp_path, board):
    from cpipe.delivery.review import observe_review
    journal, s, b, tid = reviewing(repo, tmp_path, board)
    with journal:
        # Findings are persisted as stable hashes, not arbitrary credential-bearing prose.
        finding = dict(id='', criterion='review', root_cause='value opaque-credential-123',
                       severity='medium', in_scope=True, disposition='open')
        after = observe_review(b, 'd1', tid, claimer='controller',
            execute=reviewer(lambda state: verdict(state, status='changes_requested', findings=[finding])),
            evidence_root=tmp_path / 'evidence')
        assert 'opaque-credential' not in json.dumps(after.to_dict())
        for path in (tmp_path / 'evidence').glob('*.json'):
            assert 'opaque-credential' not in path.read_text()
