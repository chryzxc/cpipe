import json
import sys

import pytest

from cpipe.delivery.hermes_worker import execute_stream, command
from .test_service import plan
from .test_artifact import repo  # noqa: F401  (fixture)


def events(*values):
    return [sys.executable, '-c', 'import json; ' + '; '.join(
        'print(' + repr(json.dumps(value)) + ', flush=True)' for value in values)]


def init(session='native-child', model='model'):
    return {'type': 'system', 'subtype': 'init', 'session_id': session, 'model': model}


def result(session='native-child', text='{"status":"done"}', **extra):
    return {'type': 'result', 'session_id': session, 'exit_code': 0, 'text': text, **extra}


def run(argv, *, expected='', model='model', timeout=3, on_session=None, heartbeat=None):
    seen = []
    output = execute_stream(argv, prompt='fixture', cwd='.', env=None,
        model=model, expected_session=expected,
        on_session=on_session or seen.append, heartbeat=heartbeat or (lambda: None),
        timeout_seconds=timeout)
    return output, seen


def test_observes_host_init_before_result():
    out, seen = run(events(init(), result()))
    assert out.session_id == 'native-child' and seen == ['native-child']
    assert out.payload == {'status': 'done'}


@pytest.mark.parametrize('argv', [events(result()),
    events(init(model='other'), result()), events(init(), init(), result()),
    events(init(), result(), result()), events(init(), result(text='not structured')),
    events(init(), result(text='{"status":"done","status":"failed"}'))])
def test_missing_mismatched_duplicate_or_failed_results_fail_closed(argv):
    with pytest.raises(ValueError):
        run(argv)


def test_an_answer_is_kept_when_hermes_rotates_the_session_or_the_turn_limit_ends_the_run():
    # Seen live: a worker that answered {"status":"blocked",...} on its last allowed turn exits 1, and
    # context compression gives the result a new session id. Both answers are the worker's own.
    out, _ = run(events(init(), result(session='compressed-child')))
    assert out.session_id == 'native-child' and out.payload == {'status': 'done'}
    out, _ = run(events(init(), result(text='{"status":"blocked","reason":"x"}', exit_code=1,
                                       error='max iterations')))
    assert out.payload == {'status': 'blocked', 'reason': 'x'}


@pytest.mark.parametrize('activity', ['implement', 'review'])
def test_a_worker_that_ends_in_prose_is_not_thrown_away(monkeypatch, activity):
    # Seen live: out of turns, the worker wrote a prose summary and Hermes exited 1; cpipe threw the
    # work away three times and handed over. A build is judged by its checks, so its prose counts as done;
    # a reviewer's session is asked for the JSON verdict, without tools.
    from dataclasses import replace
    from cpipe.delivery import hermes_worker
    from cpipe.delivery.hermes_worker import Unfinished, SALVAGE
    from .test_service import candidate
    state = replace(candidate(), activity=activity, builder_session='')
    prose = result(text='I fixed most of it; summary follows.', exit_code=1,
                   tokens=dict(input=10, output=1, total=11, cache_read=0, cache_write=0))
    answer = result(session='native-child', text='{"status":"done"}',
                    tokens=dict(input=5, output=1, total=6, cache_read=4, cache_write=0))
    calls = []
    def command(actor, cwd, resume, timeout, turns):
        calls.append((resume, turns))
        return events(init(model=actor.model), prose) if not resume else events(init(resume, actor.model), answer)
    monkeypatch.setattr(hermes_worker, 'command', command)
    real = hermes_worker.execute_stream
    prompts = []
    def stream(argv, **kw):
        prompts.append(kw['prompt'])
        return real(argv, **{**kw, 'cwd': '.', 'pidfile': None})
    monkeypatch.setattr(hermes_worker, 'execute_stream', stream)
    bound = []
    out = hermes_worker.HermesWorker()(state, on_session=bound.append, heartbeat=lambda: None, timeout_seconds=120)
    assert out.session_id == 'native-child' == bound[0] and out.payload == {'status': 'done'}
    if activity == 'implement':
        assert calls == [('', hermes_worker.TURNS['implement'])] and out.tokens['total'] == 11
    else:
        assert calls == [('', hermes_worker.TURNS['review']), ('native-child', 3)] and prompts[1] == SALVAGE
        assert out.tokens['total'] == 17
    with pytest.raises(Unfinished):  # the bare stream still reports it; only the worker repairs it
        run(events(init(), prose))


def test_builder_resume_must_report_the_bound_native_session():
    with pytest.raises(ValueError, match='session'):
        run(events(init(), result()), expected='bound-session')


def test_rejected_init_stops_child_before_delayed_work(tmp_path):
    target = tmp_path / 'should-not-exist'
    script = 'import time; print(' + repr(json.dumps(init())) + ', flush=True); time.sleep(1); open(' + repr(str(target)) + ',"w").write("no")'
    def deny(_):
        raise ValueError('cancelled authority')
    with pytest.raises(ValueError, match='cancelled authority'):
        run([sys.executable, '-c', script], on_session=deny)
    assert not target.exists()


def test_silent_worker_times_out_and_heartbeats():
    ticks = []
    with pytest.raises(ValueError, match='timeout'):
        run([sys.executable, '-c', 'import time; time.sleep(2)'], timeout=.2,
            heartbeat=lambda: ticks.append(True))
    assert ticks


def test_diagnostics_and_tool_output_are_not_returned_or_persisted():
    out, _ = run(events(init(), {'type': 'tool_result', 'output': 'API_TOKEN=PRIVATE'},
                      result()))
    assert 'PRIVATE' not in repr(out)


def test_command_uses_loaded_runtime_and_exact_frozen_actor(monkeypatch):
    from cpipe.runtime import launcher
    monkeypatch.setattr(launcher, 'module_command', lambda *args: list(args))
    actor = plan().actors[0]
    argv = command(actor, '/worktree', 'bound-session', 20)
    assert argv[:2] == ['hermes_cli.main', '-p']
    for flag, value in [('--model', actor.model), ('--provider', actor.provider),
                        ('--resume', 'bound-session'), ('--in', '/worktree')]:
        assert argv[argv.index(flag) + 1] == value
    assert '--yolo' not in argv and '--no-restore-cwd' in argv


def test_reviewer_gets_read_only_full_range_structured_contract(monkeypatch):
    from dataclasses import replace
    from cpipe.delivery import hermes_worker
    from cpipe.delivery.review import REVIEW_AXES
    from .test_service import candidate
    state = replace(candidate(), activity='review')
    captured = {}
    monkeypatch.setattr(hermes_worker, 'command', lambda *args: ['fixture'])
    def observe(argv, **kwargs):
        captured.update(kwargs)
        return 'observed'
    monkeypatch.setattr(hermes_worker, 'execute_stream', observe)
    out = hermes_worker.HermesWorker()(state, on_session=lambda _: None,
                                      heartbeat=lambda: None, timeout_seconds=20)
    assert out == 'observed'
    assert captured['expected_session'] == ''
    assert captured['model'] == state.plan.actors[1].model
    for field in ['Read-only independent review', 'verified_ids', 'changes_requested',
                  state.artifact, state.plan.base_commit, *REVIEW_AXES]:
        assert field in captured['prompt']


def test_host_reported_tokens_are_kept_for_usage():
    tokens = dict(input=1200, output=40, total=1240, cache_read=900, cache_write=0)
    out, _ = run(events(init(), result(tokens=tokens)))
    assert out.tokens == tokens
    out, _ = run(events(init(), result(tokens={'input': 'many'})))
    assert out.tokens is None


def test_resumed_builder_gets_only_the_delta_with_finding_text():
    from dataclasses import replace
    from cpipe.delivery.findings import Finding, FindingRecord
    from cpipe.delivery.hermes_worker import _prompt
    from cpipe.delivery import review
    from .test_service import candidate
    state = candidate()
    finding = Finding('REV-001', 'review', 'cause-abc', 'high', True, 'open')
    state = replace(state, activity='implement', findings=(FindingRecord(finding),))
    review.NOTES['cause-abc'] = 'off-by-one in double for negatives'
    fresh = _prompt(state, '')
    assert 'Frozen contract' in fresh and 'off-by-one' in fresh
    resumed = _prompt(state, 'builder-session')
    assert 'Frozen contract' not in resumed and 'skills_list' not in resumed
    assert 'REV-001 [high] off-by-one in double for negatives' in resumed
    assert len(resumed) < len(fresh) / 2


def test_snapshot_sees_new_files_without_touching_the_index(repo):
    from cpipe.delivery.artifact import fingerprint
    from cpipe.delivery.hermes_worker import snapshot, _git
    first = snapshot(repo)
    (repo / 'new.py').write_text('x = 1\n')
    before = fingerprint(repo)
    second = snapshot(repo)
    assert second != first and fingerprint(repo) == before
    assert _git(repo, 'diff', '--name-only', first, second) == 'new.py'
    assert _git(repo, 'status', '--porcelain') == '?? new.py'  # still untracked


def test_re_review_is_scoped_to_the_fix_diff(monkeypatch):
    from cpipe.delivery.hermes_worker import _prompt
    from .test_service import candidate, ready_for_review
    state = ready_for_review(candidate())
    assert 'report all findings in one verdict' in _prompt(state, '')
    focused = _prompt(state, '', 'aaa bbb')
    assert '`git diff aaa bbb`' in focused and 'report all findings in one verdict' not in focused



def test_builders_work_cheaply_and_steps_cap_their_turns():
    from dataclasses import replace
    from cpipe.delivery import hermes_worker
    from .test_service import candidate
    state = replace(candidate(), activity='implement')
    for resume in ('', 'session'):
        prompt = hermes_worker._prompt(state, resume)
        assert 'no background jobs' in prompt and '"environment: ' in prompt
    review = hermes_worker._prompt(replace(state, activity='review'), '')
    assert 'do not run them or the full suite again' in review
    argv = hermes_worker.command(state.plan.actors[0], '/w', '', 60, hermes_worker.TURNS['write_tests'])
    assert argv[argv.index('--max-turns') + 1] == '30'


def test_a_worker_left_running_by_a_dead_process_is_stopped(tmp_path):
    import subprocess, sys, time
    from cpipe.delivery import hermes_worker
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    orphan = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', str(tmp_path)],
                              start_new_session=True)
    pidfile = hermes_worker.worker_pidfile(tmp_path)
    pidfile.write_text(str(orphan.pid))
    hermes_worker.stop_orphan(tmp_path)
    assert orphan.wait(timeout=10) and not pidfile.exists()
    bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
    pidfile.write_text(str(bystander.pid))  # a reused process id: not working here, left alone
    hermes_worker.stop_orphan(tmp_path)
    assert bystander.poll() is None
    bystander.kill()


def test_the_test_writer_runs_the_exact_behavior_check_once():
    from cpipe.delivery.check_runner import CommandCheck
    from cpipe.delivery.hermes_worker import behavior_check
    text = behavior_check((CommandCheck('regression', ('npm', 'test')),
                           CommandCheck('behavior', ('npx', 'vitest', 'run', 'a.test.ts', '--outputFile={report}'),
                                        ('parser > rejects empty input',), env=(('HOME', '/h'),))))
    assert "`npx vitest run a.test.ts '--outputFile=<any temporary file>'`" in text
    assert 'parser > rejects empty input' in text and 'npm' not in text
    assert behavior_check(()) == ''
    pinned = CommandCheck('behavior', ('npm', 'test'), ('a',), env=(('PATH', '/nvm/bin:/usr/bin'),))
    assert '`env PATH=/nvm/bin:/usr/bin npm test`' in behavior_check((pinned,))  # the checks' own programs
