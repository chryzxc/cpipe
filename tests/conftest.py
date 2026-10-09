"""Every test runs in a throwaway HERMES_HOME with its own kanban.db (the real Hermes kanban kernel), never the
operator's live ~/.hermes or board.

    .venv/bin/python -m pytest      (.venv: Hermes' python + pytest + acp, with a .pth to hermes-agent)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_HERMES = Path(os.environ.get("HERMES_AGENT_ROOT", Path.home() / ".hermes" / "hermes-agent"))
if (_HERMES / "hermes_cli").is_dir() and str(_HERMES) not in sys.path:
    sys.path.insert(1, str(_HERMES))
from hermes_cli import _launchers
REAL_RUNTIME_COMMAND = _launchers.runtime_command


@pytest.fixture(autouse=True)
def hermes_home(tmp_path, tmp_path_factory, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    # Hermes now shares Kanban across profiles via the default user root. Round
    # workers intentionally do not inherit HERMES_KANBAN_* claim material, so
    # their fallback root must also resolve to this throwaway test home.
    monkeypatch.setenv("HOME", str(tmp_path))
    for key, value in {"GIT_AUTHOR_NAME": "Test", "GIT_COMMITTER_NAME": "Test",
                       "GIT_AUTHOR_EMAIL": "test@example.invalid",
                       "GIT_COMMITTER_EMAIL": "test@example.invalid"}.items():
        monkeypatch.setenv(key, value)
    sys.path.insert(0, str(ROOT / "tests/runtime"))
    import fake_launcher
    monkeypatch.setattr(_launchers, "runtime_command", fake_launcher.runtime_command)
    # A hermes --run-module launcher under the test interpreter exercises the
    # real detached subprocess chain without provisioning a managed runtime in
    # every throwaway home. Real launcher selection has separate unit coverage.
    launch_dir = tmp_path_factory.mktemp("cpipe-launcher")
    launch = launch_dir / "hermes"
    launch.write_text(f"#!{sys.executable}\n" + (ROOT / "tests/runtime/fake_launcher.py").read_text())
    launch.chmod(0o755)
    monkeypatch.setenv("PATH", f"{launch_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("HERMES_REAL_HOME", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    return home


@pytest.fixture
def board(hermes_home):
    pytest.importorskip("hermes_cli.kanban_db", reason="needs a Hermes Agent checkout")
    from cpipe.runtime import hermes_api as h
    h.kb.init_db()
    conn = h.connect(None)
    yield conn
    conn.close()


def make_task(conn, *, assignee: str, title: str = "t", body: str = "do it", **kw) -> str:
    from hermes_cli import kanban_db as kb
    return kb.create_task(conn, title=title, body=body, assignee=assignee,
                          **kw)


def wait_for(pred, timeout: float = 30.0, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")
