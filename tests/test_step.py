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
    for key in ('HERMES_SESSION_ID', 'CPIPE_RUN', 'CPIPE_STEP', 'CPIPE_LANE'):
        env.pop(key, None)
    result = subprocess.run(['sh', str(ROOT / 'skills/run/bin/step.sh'), str(run), str(tmp_path),
                             'build', '1', '10', '10', 'hermes', 'chat'],
                            env=env, text=True, capture_output=True, timeout=20)
    assert f'exit {status}' in result.stdout
    assert 'session_id: fixture' in result.stdout
    assert result.returncode == status
