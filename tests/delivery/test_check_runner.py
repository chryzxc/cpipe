"""Observed check receipts: exit zero alone is not proof of green behavior."""
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


def pytest_check(criteria=('test_behavior::test_double',), timeout=120, python=''):
    from cpipe.delivery.check_runner import CommandCheck, REPORT
    return CommandCheck('behavior', (python or sys.executable, '-m', 'pytest', 'test_behavior.py', '-q',
                                     '-p', 'no:cacheprovider', '--junitxml=' + REPORT), criteria, timeout)


def setup_check(repo, tmp_path, source=None, *, timeout=20,
                criteria=('test_behavior::test_double',), write_paths=None, python='', spec=None):
    from cpipe.delivery.artifact import fingerprint
    (repo / 'test_behavior.py').write_text(source or
        'from double import double\ndef test_double():\n    assert double(2) == 4\n')
    spec = spec or pytest_check(criteria, timeout, python)
    now = int(time.time())
    p = plan(worktree=str(repo), common_git_dir=str(repo / '.git'),
             base_commit=git(repo, 'rev-parse', 'HEAD'), expires_at=now + 300,
             envelope=replace(plan().envelope, required=(spec.obligation,)),
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
    from cpipe.delivery.check_runner import observe_check
    return observe_check(state, spec, evidence_root=tmp_path / 'evidence')


def test_real_assertion_failure_produces_behavioral_red_receipt(repo, tmp_path):
    journal, service, state, spec = setup_check(repo, tmp_path)
    with journal:
        observed = execute(state, spec, tmp_path)
        assert observed.outcome is Outcome.FAIL
        assert observed.observed is True
        assert observed.artifact == state.artifact
        assert observed.command == spec.identity().command


def test_checks_run_the_projects_own_command(repo, tmp_path):
    marker = tmp_path / 'used'
    python = tmp_path / 'bin' / 'python'
    python.parent.mkdir()
    python.write_text(f'#!/bin/sh\ntouch {marker}\nexec {sys.executable} "$@"\n')
    python.chmod(0o755)
    journal, service, state, spec = setup_check(repo, tmp_path, python=str(python))
    with journal:
        assert spec.command('r')[0] == str(python)
        assert execute(state, spec, tmp_path).outcome is Outcome.FAIL
    assert marker.exists()


def test_missing_program_is_rejected_before_any_run():
    from cpipe.delivery.check_runner import CommandCheck
    with pytest.raises(ValueError, match='not installed'):
        CommandCheck('behavior', ('cpipe-no-such-runner', 'test'), ('t',)).identity()
    with pytest.raises(ValueError, match='PATH or an absolute'):
        CommandCheck('behavior', ('node_modules/.bin/vitest',), ('t',))


def sh(script, criteria=('price rounds up',), report=False):
    """A non-Python runner stand-in; $1 is the private report path when requested."""
    from cpipe.delivery.check_runner import CommandCheck, REPORT
    return CommandCheck('behavior', ('sh', '-c', script, 'check') + ((REPORT,) if report else ()), criteria, 20)


@pytest.mark.parametrize('script, outcome', [
    ('echo "FAIL price rounds up"; exit 1', Outcome.FAIL),
    ('echo "ok price rounds up"; exit 0', Outcome.PASS),
    ('echo "error TS2304: cannot find name"; exit 1', Outcome.UNAVAILABLE),  # never ran: not RED
    ('echo "ok"; exit 0', Outcome.UNAVAILABLE),
    ('echo "price rounds up"; exit 127', Outcome.UNAVAILABLE),
])
def test_exit_code_checks_need_their_criteria_in_the_output(repo, tmp_path, script, outcome):
    journal, service, state, spec = setup_check(repo, tmp_path, spec=sh(script))
    with journal:
        assert execute(state, spec, tmp_path).outcome is outcome


JUNIT = ('<testsuites><testsuite name="src/price.test.ts" tests="2">'
         '<testcase classname="src/price.test.ts" name="price rounds up">{case}</testcase>'
         '<testcase classname="src/price.test.ts" name="price keeps cents"/></testsuite></testsuites>')


@pytest.mark.parametrize('case, code, outcome', [
    ('<failure message="expected 2 to be 3"/>', 1, Outcome.FAIL),
    ('<failure>Error: expect(received).toBe(expected)</failure>', 1, Outcome.FAIL),
    ('', 0, Outcome.PASS),
    ('<failure message="TypeError: price is not a function"/>', 1, Outcome.UNAVAILABLE),
    ('<skipped/>', 0, Outcome.UNAVAILABLE),
])
def test_any_runner_junit_report_decides_the_outcome(repo, tmp_path, case, code, outcome):
    report = tmp_path / 'junit.xml'
    report.write_text(JUNIT.format(case=case))
    spec = sh(f'cp {report} "$1"; exit {code}', ('src/price.test.ts::price rounds up',), report=True)
    journal, service, state, spec = setup_check(repo, tmp_path, spec=spec)
    with journal:
        assert execute(state, spec, tmp_path).outcome is outcome


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


VITEST = ('<testsuites><testsuite name="tests/price.test.js" tests="2">'
          '<testcase classname="tests/price.test.js" name="price &gt; rounds up">{case}</testcase>'
          '<testcase classname="tests/price.test.js" name="price &gt; keeps cents"/></testsuite></testsuites>')


@pytest.mark.parametrize('criterion, case, code, outcome, reason', [
    # vitest names cases "describe > title"; the bare title is the approved criterion.
    ('rounds up', '<failure message="expected 2 to be 3" type="AssertionError"/>', 1, Outcome.FAIL, ''),
    ('price > rounds up', '', 0, Outcome.PASS, ''),
    ('rounds up', '<failure message="price is not a function" type="TypeError"/>', 1,
     Outcome.UNAVAILABLE, 'test_crashed'),
    ('rounds down', '', 0, Outcome.UNAVAILABLE, 'criterion_not_found'),
    ('s up', '', 0, Outcome.UNAVAILABLE, 'criterion_not_found'),  # whole words only
])
def test_vitest_reports_match_titles_and_say_why_unavailable(repo, tmp_path, criterion, case, code, outcome, reason):
    report = tmp_path / 'junit.xml'
    report.write_text(VITEST.format(case=case))
    spec = sh(f'cp {report} "$1"; exit {code}', (criterion,), report=True)
    journal, service, state, spec = setup_check(repo, tmp_path, spec=spec)
    with journal:
        receipt = execute(state, spec, tmp_path)
        assert (receipt.outcome, receipt.reason) == (outcome, reason)


def test_missing_command_says_so(repo, tmp_path):
    journal, service, state, spec = setup_check(repo, tmp_path, spec=sh('exit 127'))
    with journal:
        assert execute(state, spec, tmp_path).reason == 'command_not_found'


def test_failures_already_there_before_the_change_do_not_block(repo, tmp_path):
    from dataclasses import replace as swap
    from cpipe.delivery.check_runner import take_baseline
    out = tmp_path / 'out'
    out.write_text('other/app.vue\n  1:1  error  name should be multi-word\n')
    lint = swap(sh(f'cat {out}; exit 1', ()), obligation='lint')
    report = tmp_path / 'junit.xml'
    report.write_text(JUNIT.format(case='').replace('<testcase classname="src/price.test.ts" name="price keeps cents"/>',
        '<testcase classname="src/price.test.ts" name="price keeps cents"><failure/></testcase>'))
    unit = swap(sh(f'cp {report} "$1"; exit 1', ('price rounds up',), report=True), obligation='regression')
    for spec, new in ((lint, 'mine.vue\n  3:1  error  unused variable\n'),
                      (unit, '<testcase classname="src/x.test.ts" name="x"><failure/></testcase></testsuite>')):
        journal, service, state, spec = setup_check(repo, tmp_path, spec=spec)
        with journal:
            before = execute(state, spec, tmp_path)
            assert before.outcome is not Outcome.PASS  # no baseline yet: every failure counts
            take_baseline(state, (spec,), evidence_root=tmp_path / 'evidence')
            if spec is lint:
                out.write_text(out.read_text().replace('1:1', '2:7'))  # same error, moved
            assert (execute(state, spec, tmp_path).outcome, execute(state, spec, tmp_path).reason) == (
                Outcome.PASS, 'pre_existing')
            target = out if spec is lint else report
            target.write_text(target.read_text() + new if spec is lint
                              else target.read_text().replace('</testsuite>', new, 1))
            assert execute(state, spec, tmp_path).outcome is not Outcome.PASS  # a new failure blocks
        (tmp_path / 'evidence' / 'baseline').joinpath('d1.json').unlink()
        (tmp_path / 'checks.db').unlink()
