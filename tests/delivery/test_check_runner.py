"""Observed pytest receipts: exit zero alone is not proof of green behavior."""
from dataclasses import replace
from pathlib import Path
import sys
import time

import pytest

from cpipe.delivery.contracts import Outcome
from cpipe.delivery.journal import Journal
from cpipe.delivery.service import Event, Service
from .test_artifact import repo, git
from .test_execution_plan import plan


def setup_check(repo, tmp_path, source=None, *, timeout=20,
                criteria=('test_behavior::test_double',), write_paths=None, python=''):
    from cpipe.delivery.artifact import fingerprint
    from cpipe.delivery.check_runner import PytestCheck
    (repo / 'test_behavior.py').write_text(source or
        'from double import double\ndef test_double():\n    assert double(2) == 4\n')
    spec = PytestCheck('behavior', ('test_behavior.py',),
                       criteria, timeout_seconds=timeout, python=python)
    now = int(time.time())
    p = plan(worktree=str(repo), common_git_dir=str(repo / '.git'),
             base_commit=git(repo, 'rev-parse', 'HEAD'), expires_at=now + 300,
             envelope=replace(plan().envelope, required=('behavior',)),
             checks=(spec.identity(),), write_paths=write_paths or plan().write_paths)
    journal = Journal(tmp_path / 'checks.db')
    service = Service(journal)
    state, _ = service.accept(Event('proposal', 'proposal_created', 'd1', p.digest(),
        fingerprint(repo).digest, 0, now, session_id='s1', profile_id='default', plan=p))
    state, _ = service.accept(Event('approval', 'envelope_approved', 'd1', p.digest(),
        state.artifact, state.version, now, session_id='s1', profile_id='default',
        activity_id=state.active_intent))
    return journal, service, state, spec


def fixed(repo):
    """Fake builder that applies the fix to the seeded double() bug."""
    from cpipe.delivery.workers import WorkerResult
    def execute(state, *, on_session, heartbeat, **kw):
        on_session('builder-session')
        heartbeat()
        (repo / 'double.py').write_text('def double(n): return n * 2\n')
        return WorkerResult('builder-session', {'status': 'done'})
    return execute


def red_then_implemented(b, repo, tmp_path, spec, claimer='controller'):
    """Observe the behavior RED, then one in-scope builder fix; checks are next."""
    from cpipe.runtime import hermes_api as h
    from cpipe.delivery.workers import observe_implementation
    tid = b.schedule('d1')
    h.claim_task(b.conn, tid, claimer=claimer, ttl_seconds=60)
    b.bind_claim('d1', tid, claimer=claimer, worker_session='check-red')
    assert b.observe_checks('d1', tid, claimer=claimer, checks=(spec,),
                            evidence_root=tmp_path / 'evidence').activity == 'implement'
    state = observe_implementation(b, 'd1', b.schedule('d1'), claimer=claimer, execute=fixed(repo))
    assert state.activity == 'checks'
    return state


def execute(state, spec, tmp_path):
    from cpipe.delivery.check_runner import observe_pytest
    return observe_pytest(state, spec, evidence_root=tmp_path / 'evidence')


def test_real_assertion_failure_produces_behavioral_red_receipt(repo, tmp_path):
    journal, service, state, spec = setup_check(repo, tmp_path)
    with journal:
        observed = execute(state, spec, tmp_path)
        assert observed.outcome is Outcome.FAIL
        assert observed.observed is True
        assert observed.artifact == state.artifact
        assert observed.command == spec.identity().command


def test_checks_run_under_the_project_interpreter(repo, tmp_path):
    marker = tmp_path / 'used'
    python = tmp_path / 'bin' / 'python'
    python.parent.mkdir()
    python.write_text(f'#!/bin/sh\ntouch {marker}\nexec {sys.executable} "$@"\n')
    python.chmod(0o755)
    journal, service, state, spec = setup_check(repo, tmp_path, python=str(python))
    with journal:
        assert spec.argv('r')[0] == str(python)
        assert execute(state, spec, tmp_path).outcome is Outcome.FAIL
    assert marker.exists()


def test_interpreter_without_pytest_is_rejected_before_any_run(tmp_path):
    import subprocess
    from cpipe.delivery.check_runner import PytestCheck
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(tmp_path / 'bare')], check=True)
    spec = PytestCheck('behavior', ('test_behavior.py',), ('test_behavior::test_double',),
                       python=str(tmp_path / 'bare' / 'bin' / 'python'))
    with pytest.raises(ValueError, match='no pytest'):
        spec.identity()
    with pytest.raises(ValueError, match='absolute'):
        PytestCheck('behavior', ('t.py',), ('t::x',), python='.venv/bin/python')


def test_real_behavior_pass_on_new_artifact_produces_green(repo, tmp_path):
    journal, service, state, spec = setup_check(repo, tmp_path)
    with journal:
        (repo / 'double.py').write_text('def double(n): return n * 2\n')
        from cpipe.delivery.artifact import fingerprint
        state = replace(state, artifact=fingerprint(repo).digest)
        observed = execute(state, spec, tmp_path)
        assert observed.outcome is Outcome.PASS and observed.observed is True


@pytest.mark.parametrize('source', [
    'import pytest\n@pytest.mark.skip(reason="not observed")\ndef test_double(): pass\n',
    'def test_other(): pass\n',
    'raise RuntimeError("collection failure")\n',
    'def test_double(): raise RuntimeError("not behavioral RED")\n',
    'def test_double(): raise RuntimeError("AssertionError")\n',
])
def test_skips_missing_criteria_and_harness_failures_never_become_green_or_red(repo, tmp_path, source):
    journal, service, state, spec = setup_check(repo, tmp_path, source)
    with journal:
        observed = execute(state, spec, tmp_path)
        assert observed.outcome is Outcome.UNAVAILABLE
        assert observed.observed is False


def test_timeout_is_unavailable_not_failed_behavior(repo, tmp_path):
    source = 'import time\ndef test_double(): time.sleep(5)\n'
    journal, service, state, spec = setup_check(repo, tmp_path, source, timeout=1)
    with journal:
        observed = execute(state, spec, tmp_path)
        assert observed.outcome is Outcome.UNAVAILABLE and not observed.observed


def test_check_cannot_mutate_git_visible_artifact(repo, tmp_path):
    source = ('from pathlib import Path\ndef test_double():\n'
              '    Path("double.py").write_text("changed")\n    assert True\n')
    journal, service, state, spec = setup_check(repo, tmp_path, source)
    with journal, pytest.raises(ValueError, match='artifact changed'):
        execute(state, spec, tmp_path)


def test_stale_artifact_or_changed_definition_does_not_execute(repo, tmp_path):
    journal, service, state, spec = setup_check(repo, tmp_path)
    with journal:
        from cpipe.delivery.check_runner import PytestCheck
        with pytest.raises(ValueError, match='definition'):
            execute(state, replace(spec, criteria=('test_behavior::not_approved',)), tmp_path)
        (repo / 'double.py').write_text('stale edit')
        with pytest.raises(ValueError, match='artifact'):
            execute(state, spec, tmp_path)
        assert not (tmp_path / 'evidence').exists()


def test_unapproved_or_non_check_activity_never_executes(repo, tmp_path):
    journal, service, state, spec = setup_check(repo, tmp_path)
    with journal:
        with pytest.raises(ValueError, match='approved'):
            execute(replace(state, approved=False), spec, tmp_path)
        with pytest.raises(ValueError, match='activity'):
            execute(replace(state, activity='review'), spec, tmp_path)


def test_credential_shaped_parameter_ids_are_not_retained_in_evidence(repo, tmp_path):
    source = ('import pytest\n'
              '@pytest.mark.parametrize("value", [1], ids=["DO_NOT_RETAIN_TEST_VALUE"])\n'
              'def test_double(value): assert value == 1\n')
    journal, service, state, spec = setup_check(repo, tmp_path, source,
        criteria=('test_behavior::test_double[DO_NOT_RETAIN_TEST_VALUE]',))
    with journal:
        assert execute(state, spec, tmp_path).outcome is Outcome.PASS
        for path in (tmp_path / 'evidence').glob('*.json'):
            contents = path.read_text()
            assert 'DO_NOT_RETAIN_TEST_VALUE' not in contents
            assert 'criteria_fingerprints' in contents and '[REDACTED]' in contents


def test_credentials_and_test_output_are_not_retained_in_evidence(repo, tmp_path):
    source = ('def test_double():\n    print("password=DO_NOT_RETAIN_TEST_VALUE")\n'
              '    assert False, "token=DO_NOT_RETAIN_TEST_VALUE"\n')
    journal, service, state, spec = setup_check(repo, tmp_path, source)
    with journal:
        assert execute(state, spec, tmp_path).outcome is Outcome.FAIL
        evidence = list((tmp_path / 'evidence').rglob('*'))
        assert evidence and all(p.suffix != '.xml' for p in evidence)
        assert not any('DO_NOT_RETAIN_TEST_VALUE' in p.read_text() for p in evidence if p.is_file())
        assert '[REDACTED]' in next(p for p in evidence if p.suffix == '.json').read_text()
