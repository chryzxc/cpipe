import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('status', [0, 7, 124, 127])
def test_role_launcher_preserves_exit_status(tmp_path, status):
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'now.md').write_text('## Instructions\n## Step\nBuild\n')
    (run / 'brief-build-1.md').write_text('Test brief')
    binary = tmp_path / 'hermes'
    binary.write_text(f'#!/bin/sh\necho session_id: fixture\nexit {status}\n')
    binary.chmod(0o755)
    env = dict(os.environ, PATH=f'{tmp_path}{os.pathsep}{os.environ["PATH"]}', HERMES_HOME=str(tmp_path))
    for key in ('HERMES_SESSION_ID', 'CPIPE_RUN', 'CPIPE_STEP'):
        env.pop(key, None)
    result = subprocess.run(['sh', str(ROOT / 'skills/run/bin/step.sh'), str(run), str(tmp_path),
                             'build', '1', '10', '10', 'hermes', 'chat'],
                            env=env, text=True, capture_output=True, timeout=20)
    assert f'exit {status}' in result.stdout
    assert 'session_id: fixture' in result.stdout
    assert result.returncode == status


def test_role_launcher_records_profile_and_session(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'now.md').write_text('## Instructions\n## Step\nBuild\n')
    (run / 'brief-build-1.md').write_text('Test brief')
    binary = tmp_path / 'hermes'
    binary.write_text('#!/bin/sh\necho session_id: fixture\n')
    binary.chmod(0o755)
    env = dict(os.environ, PATH=f'{tmp_path}{os.pathsep}{os.environ["PATH"]}', HERMES_HOME=str(tmp_path))
    result = subprocess.run(['sh', str(ROOT / 'skills/run/bin/step.sh'), str(run), str(tmp_path),
                             'build', '1', '10', '10', 'hermes', '-p', 'builder', 'chat'],
                            env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0
    log = (run / 'logs' / 'build-1.txt').read_text()
    assert 'cpipe_profile: builder' in log
    assert 'session_id: fixture' in log


@pytest.mark.parametrize('profile,args', [('default', []), ('builder', ['--resume', 'earlier'])])
def test_launcher_records_inherited_profile_and_resume(tmp_path, profile, args):
    run = tmp_path / 'run'; run.mkdir()
    (run / 'now.md').write_text('## Instructions\n## Step\nBuild\n')
    (run / 'brief-build-1.md').write_text('Test brief')
    binary = tmp_path / 'hermes'
    binary.write_text('#!/bin/sh\necho session_id: fixture\n'); binary.chmod(0o755)
    home = tmp_path / 'home' if profile == 'default' else tmp_path / 'home/profiles/builder'
    env = dict(os.environ, PATH=f'{tmp_path}{os.pathsep}{os.environ["PATH"]}', HERMES_HOME=str(home))
    result = subprocess.run(['sh', str(ROOT / 'skills/run/bin/step.sh'), str(run), str(tmp_path),
                             'build', '1', '10', '10', 'hermes', 'chat', *args],
                            env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0
    assert f'cpipe_profile: {profile}' in result.stdout
    assert f'cpipe_resume: {"earlier" if args else "new"}' in result.stdout


@pytest.mark.parametrize('child,expected', [(0, 9), (7, 7)])
def test_diagnostics_do_not_hide_child_failure(tmp_path, child, expected):
    run = tmp_path / 'run'; run.mkdir()
    for name, script in [('hermes', f'#!/bin/sh\necho session_id: fixture\nexit {child}\n'),
                         ('python3', '#!/bin/sh\n[ "$2" = rounds ] && exit 9\nprintf "query\\n"\n')]:
        binary = tmp_path / name; binary.write_text(script); binary.chmod(0o755)
    env = dict(os.environ, PATH=f'{tmp_path}{os.pathsep}{os.environ["PATH"]}')
    result = subprocess.run(['sh', str(ROOT / 'skills/run/bin/step.sh'), str(run), str(tmp_path),
                             'build', '1', '10', '10', 'hermes', 'chat'],
                            env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == expected
    assert f'exit {child}' in result.stdout


def test_wait_returns_once_every_step_log_has_its_exit_line(tmp_path):
    (tmp_path / 'logs').mkdir()
    done, late = tmp_path / 'logs/review-1.txt', tmp_path / 'logs/security-1.txt'
    done.write_text('the reply\nsession_id: a\nexit 0\n')
    late.write_text('working\n')
    gave_up = subprocess.run(['sh', str(ROOT / 'skills/run/bin/wait.sh'), str(tmp_path), '1', str(done), str(late)],
                             text=True, capture_output=True, timeout=20)
    assert gave_up.returncode == 3 and gave_up.stdout.strip() == f'still running: {late}'
    wait = subprocess.Popen(['sh', str(ROOT / 'skills/run/bin/wait.sh'), str(tmp_path), '60', str(done), str(late)],
                            text=True, stdout=subprocess.PIPE)
    with pytest.raises(subprocess.TimeoutExpired):
        wait.wait(timeout=2)  # security still runs
    late.write_text('working\nsession_id: b\nexit 2 (no query: see the line above)\n')
    out, _ = wait.communicate(timeout=20)
    assert 'the reply' in out and 'exit 2 (no query' in out and out.rstrip().splitlines()[-1].startswith('Rounds')
