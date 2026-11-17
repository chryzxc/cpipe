"""Cheap coordination without weaker delivery evidence: launcher validation, honest check status, honest report."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


facts = load('efficiency_facts', ROOT / 'skills/run/bin/cpipe.py')
plugin = load('efficiency_plugin', ROOT / '__init__.py')


@pytest.fixture(autouse=True)
def isolated_host(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    for key in ('HERMES_SESSION_ID', 'CPIPE_RUN', 'CPIPE_STEP'):
        monkeypatch.delenv(key, raising=False)


def launch(tmp_path, *fields):
    binary = tmp_path / 'hermes'
    binary.write_text(f'#!/bin/sh\ntouch "{tmp_path}/started"\nprintf "arg: %s\\n" "$@"\necho session_id: fixture\n')
    binary.chmod(0o755)
    env = dict(os.environ, PATH=f'{tmp_path}{os.pathsep}{os.environ["PATH"]}')
    return subprocess.run(['sh', str(ROOT / 'skills/run/bin/step.sh'), *fields], text=True,
                          capture_output=True, env=env, timeout=20)


@pytest.mark.parametrize('who,extra', [
    (['hermes', 'chat'], []),
    (['hermes', '-p', 'planner', 'chat'], []),
    (['chat'], []),
    (['-p', 'planner'], []),
    (['hermes', '--profile', 'planner', 'chat'], []),
    (['hermes', 'chat'], ['--resume', 'earlier', '--reasoning', 'high']),
])
def test_launcher_accepts_every_documented_form(tmp_path, who, extra):
    run = tmp_path / 'run'; run.mkdir()
    (run / 'brief-plan-1.md').write_text('Plan this task.')
    result = launch(tmp_path, str(run), str(tmp_path), 'plan', '1', '10', '10', *who, *extra)
    assert result.returncode == 0, result.stdout + result.stderr
    log = (run / 'logs/plan-1.txt').read_text()
    assert log.count('arg: chat\n') == 1
    assert ('arg: --run-budget\n' in log) == ('--resume' not in extra)


@pytest.mark.parametrize('fields', [
    ['plan', 'one', '10', '10', 'hermes', 'chat'],
    ['plan', '1', 'hermes', '10', 'chat'],
    ['plan', '1', '10', '0', 'hermes', 'chat'],
    ['../plan', '1', '10', '10', 'hermes', 'chat'],
    ['plan', '1', '10', '10', 'hermes', '-p'],
    ['plan', '1', '10', '10', 'hermes', 'chat', '--resume'],
    ['plan', '1', '10', '10', 'hermes', 'chat', '--query-file', 'wrong'],
    ['plan', '1', '10', '10', 'hermes', 'chat', '-m', '--provider', 'wrong'],
    ['plan', '1', '10', '10', 'hermes', 'chat', '--resume', ''],
    ['plan', '1', '10', '10', 'hermes', 'chat', '--run-budget', '0'],
    ['plan', '1', '10', '00', 'hermes', 'chat'],
    ['plan', '1', '10'],
])
def test_launcher_rejects_a_malformed_launch_before_any_session(tmp_path, fields):
    run = tmp_path / 'run'; run.mkdir()
    result = launch(tmp_path, str(run), str(tmp_path), *fields)
    assert result.returncode == 2 and 'usage: step.sh' in result.stderr
    assert not (tmp_path / 'started').exists() and not (run / 'logs').exists()


def test_launcher_rejects_a_missing_run_dir(tmp_path):
    result = launch(tmp_path, str(tmp_path / 'absent'), str(tmp_path), 'plan', '1', '10', '10', 'hermes', 'chat')
    assert result.returncode == 2 and 'run dir not found' in result.stderr


@pytest.mark.parametrize('role', ['plan-review', 'custom_review'])
def test_launcher_does_not_restrict_a_users_role_names(tmp_path, role):
    run = tmp_path / 'run'; run.mkdir()
    (run / f'brief-{role}-1.md').write_text('Review the plan.')
    result = launch(tmp_path, str(run), str(tmp_path), role, '1', '10', '10', 'hermes', 'chat')
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('code', [0, 7, 124, 127])
def test_check_helper_exits_with_the_checks_own_status(tmp_path, code):
    command = tmp_path / 'check'
    command.write_text(f'#!/bin/sh\necho summary\necho diagnostic >&2\nexit {code}\n')
    command.chmod(0o755)
    log = tmp_path / 'logs/check.txt'
    result = subprocess.run(['sh', str(ROOT / 'skills/run/bin/check.sh'), '10', str(log), str(command)],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == code
    assert f'exit {code}' in result.stdout
    assert log.read_text() == f'summary\ndiagnostic\nexit {code}\n'


def test_check_helper_rejects_missing_arguments(tmp_path):
    result = subprocess.run(['sh', str(ROOT / 'skills/run/bin/check.sh'), '10', str(tmp_path / 'x.txt')],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 2 and 'usage: check.sh' in result.stderr


def test_check_helper_really_times_out_a_hanging_check(tmp_path):
    command = tmp_path / 'hang'
    command.write_text('#!/bin/sh\nsleep 60\n')
    command.chmod(0o755)
    log = tmp_path / 'timeout.txt'
    result = subprocess.run(['sh', str(ROOT / 'skills/run/bin/check.sh'), '1', str(log), str(command)],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 124 and log.read_text().endswith('exit 124\n')


def test_check_does_not_start_if_its_log_cannot_be_written(tmp_path):
    command = tmp_path / 'check'
    marker = tmp_path / 'started'
    command.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    command.chmod(0o755)
    result = subprocess.run(['sh', str(ROOT / 'skills/run/bin/check.sh'), '10', str(tmp_path), str(command)],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode != 0 and not marker.exists()


def prepared(run, step='qa'):
    (run / 'spec.md').write_text('# Spec\n')
    (run / 'results').mkdir()
    result = dict(kind='tests', passed=['regression'], failed=[], errors=[], skipped=[])
    for tree in ('base', 'head'):
        (run / f'results/unit.{tree}.json').write_text(json.dumps(result))
    return facts.track(run, 'add', [step, 'Visible scrolling is smooth'])


def test_a_requirement_the_machine_could_not_run_is_not_reported_as_done(tmp_path):
    ident = prepared(tmp_path)
    facts.track(tmp_path, 'set', [ident, 'skip', 'machine: no isolated app instance to scroll'])
    assert facts.gate(tmp_path).startswith('PASS')  # the loop is finished: nothing more this machine can do
    skeleton = facts.report(tmp_path)
    assert skeleton.startswith(f'⚠️ Done except not verified here ({ident}): ')
    assert 'Visible scrolling is smooth | machine: no isolated app instance to scroll' in skeleton
    filled = re.sub(r'<[^<>\n]+>', 'written', skeleton)
    (tmp_path / 'report.md').write_text(filled.replace('⚠️ Done except not verified', '✅ Done'))
    assert facts.check_report(tmp_path).startswith('FIX:')
    (tmp_path / 'report.md').write_text(filled)
    assert facts.check_report(tmp_path).startswith('PASS')


def test_a_verified_requirement_still_reports_done(tmp_path):
    ident = prepared(tmp_path)
    facts.track(tmp_path, 'set', [ident, 'done', 'before/after scroll comparison: qa/scroll.txt'])
    assert facts.report(tmp_path).startswith('✅ Done: ')


@pytest.mark.parametrize('checks,note', [
    (None, 'CI not recorded'), ([], 'CI has no checks'), ([{'name': 'unit', 'bucket': 'pass'}], 'CI green'),
])
def test_the_gate_names_what_it_read_about_ci(tmp_path, checks, note):
    ident = prepared(tmp_path)
    facts.track(tmp_path, 'set', [ident, 'done', 'compared live'])
    if checks is not None:
        (tmp_path / 'results/ci.json').write_text(json.dumps(checks))
    assert facts.gate(tmp_path).endswith(note)


@pytest.mark.parametrize('resume', [False, True])
def test_every_brief_points_at_the_acceptance_contract(tmp_path, resume):
    (tmp_path / 'brief-build-1.md').write_text('Fix only the named regression.')
    (tmp_path / 'now.md').write_text('## Instructions\n- [ ] Preserve existing behavior\n')
    query = facts.brief(str(tmp_path), '/worktree', 'build', 1, resume=resume)
    assert f'{tmp_path}/spec.md' in query and f'{tmp_path}/plan.md' in query
    assert 'not-verified' in query and 'Fix only the named regression.' in query
    assert ('Preserve existing behavior' in query) == (not resume)


def test_the_watcher_stops_after_give_up_identical_nudges(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'gateway.session_context',
                        types.SimpleNamespace(get_session_env=lambda _: None))
    monkeypatch.setattr(plugin, '_run_dir', lambda _: tmp_path)
    monkeypatch.setattr(plugin, '_gate', lambda *_: 'FIX: R1')
    monkeypatch.setattr(plugin, '_waiting', lambda _: False)
    monkeypatch.setattr(plugin, '_subagent', lambda _: False)
    (tmp_path / 'track.json').write_text('[]')
    sent = []
    ctx = types.SimpleNamespace(inject_message=lambda text, **_: sent.append(text) or True)
    history = [{'role': 'user', 'content': plugin.START.format(skill='s', root='r') + 'fix it'}]
    for _ in range(12):
        history.extend([{'role': 'user', 'content': 'go'}, {'role': 'assistant', 'content': 'Still pending.'}])
        plugin._watch(ctx, session_id='s', user_message='go', assistant_response='Still pending.',
                      conversation_history=history)
    assert len(sent) == plugin.GIVE_UP
    assert 'no change in the gate or the checklist' in sent[plugin.STUCK_AT - 1]
    assert 'no change in the gate' not in sent[0]


def test_implemented_is_not_verified(tmp_path):
    ident = prepared(tmp_path)
    facts.track(tmp_path, 'set', [ident, 'implemented', 'Layout request removed; live QA is next'])
    assert ident in facts.track(tmp_path, 'open', [])
    assert facts.gate(tmp_path).startswith('FIX:')
    assert facts.report(tmp_path).startswith('not ready')
    facts.track(tmp_path, 'set', [ident, 'not-verified', 'machine: no isolated simulator'])
    assert facts.report(tmp_path).startswith('⚠️ Done except not verified here')


def test_unverified_is_only_for_a_machine_limitation(tmp_path):
    ident = prepared(tmp_path)
    with pytest.raises(SystemExit, match='machine:'):
        facts.track(tmp_path, 'set', [ident, 'not-verified', 'Too much work'])


def test_gate_cannot_call_unknown_or_baseline_failing_ci_green(tmp_path):
    ident = prepared(tmp_path)
    facts.track(tmp_path, 'set', [ident, 'done', 'Compared live'])
    path = tmp_path / 'results/ci.json'
    path.write_text('[{"name":"unit","bucket":"unrecognized"}]')
    assert 'unknown state' in facts.gate(tmp_path)
    path.write_text('[{"name":"unit","bucket":"fail"}]')
    (tmp_path / 'results/ci-base.json').write_text(path.read_text())
    verdict = facts.gate(tmp_path)
    assert verdict.startswith('PASS') and 'baseline' in verdict and 'CI green' not in verdict


def test_duplicate_callback_is_not_a_new_watch_turn(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'gateway.session_context',
                        types.SimpleNamespace(get_session_env=lambda _: None))
    monkeypatch.setattr(plugin, '_run_dir', lambda _: tmp_path)
    monkeypatch.setattr(plugin, '_gate', lambda *_: 'FIX: R1')
    monkeypatch.setattr(plugin, '_waiting', lambda _: False)
    monkeypatch.setattr(plugin, '_subagent', lambda _: False)
    (tmp_path / 'track.json').write_text('[]')
    sent = []
    ctx = types.SimpleNamespace(inject_message=lambda text, **_: sent.append(text) or True)
    history = [{'role': 'user', 'content': plugin.START.format(skill='s', root='r') + 'fix it'}]
    for _ in range(4):
        plugin._watch(ctx, session_id='s', user_message='go', assistant_response='Still pending.',
                      conversation_history=history)
    assert len(sent) == 1
    history.extend([{'role': 'assistant', 'content': 'Still pending.'}, {'role': 'user', 'content': sent[-1]}])
    plugin._watch(ctx, session_id='s', user_message='go', assistant_response='Still pending.',
                  conversation_history=history)
    assert len(sent) == 2


@pytest.mark.parametrize('seconds', ['0', '00', '-1', 'not-a-time'])
def test_check_helper_rejects_an_unbounded_timeout(tmp_path, seconds):
    result = subprocess.run(['sh', str(ROOT / 'skills/run/bin/check.sh'), seconds,
                             str(tmp_path / 'log'), 'true'], text=True, capture_output=True, timeout=10)
    assert result.returncode == 2


def test_implemented_outcomes_survive_context_compaction(tmp_path, monkeypatch):
    ident = prepared(tmp_path)
    facts.track(tmp_path, 'set', [ident, 'implemented', 'Code changed; live check is still owed'])
    monkeypatch.setattr(plugin, '_run_dir', lambda _: tmp_path)
    monkeypatch.setattr(plugin, '_run_git', lambda _: tmp_path)
    monkeypatch.setattr(plugin, '_rules', lambda _: [])
    assert f'{ident} Visible scrolling is smooth' in plugin._pinned({'session_id': 'fixture'})
