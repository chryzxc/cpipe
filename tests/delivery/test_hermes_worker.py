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


@pytest.mark.parametrize('argv', [events(result()), events(init(), result(session='other')),
    events(init(model='other'), result()), events(init(), init(), result()),
    events(init(), result(), result()), events(init(), result(error='private diagnostic')),
    events(init(), result(text='not structured')),
    events(init(), result(text='{"status":"done","status":"failed"}'))])
def test_missing_mismatched_duplicate_or_failed_results_fail_closed(argv):
    with pytest.raises(ValueError):
        run(argv)


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

