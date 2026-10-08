"""Real daemon startup and provenance-based dispatch, without LLM or live cards."""
import json
import os
import signal

import pytest

from cpipe import settings
from cpipe.runtime import activation, scheduler, hermes_api as h
from cpipe.runtime.config import load_config
from conftest import make_task, wait_for


def test_runner_auto_starts_once_and_reports_actual_health(hermes_home, monkeypatch):
    # Exercise the real daemon in the isolated test root without provisioning a
    # second managed Hermes dependency tree. Launcher selection is tested separately.
    import sys
    monkeypatch.setattr(activation, "module_command", lambda module, *args: [sys.executable, "-m", module, *args])
    health = activation.ensure_running(timeout=10)
    try:
        assert activation.readiness()["pid"] == health["pid"]
        assert activation.ensure_running()["pid"] == health["pid"]
        assert activation.health_path().is_file()
    finally:
        os.kill(health["pid"], signal.SIGTERM)
        wait_for(lambda: not activation.health_path().exists(), timeout=10)


def test_round_lane_claims_plain_bot_using_delivery_provenance(board, monkeypatch):
    from types import SimpleNamespace
    tid = make_task(board, assignee="archon", created_by="cpipe")
    h.comment(board, tid, "hermes", author="cpipe:engine")
    unrelated = make_task(board, assignee="archon", created_by="user")
    cfg = load_config()
    launches = []
    daemon = scheduler.Scheduler(cfg)
    monkeypatch.setattr(daemon, "_spawn", lambda board, task, *args: launches.append(task) or SimpleNamespace(pid=os.getpid()))
    result = daemon.tick()
    assert not result.errors
    assert launches == [tid]
    assert unrelated not in launches


def test_missing_runner_reports_failure_instead_of_silent_ready(hermes_home, monkeypatch):
    from cpipe import tools
    # Restore the production helper replaced by the command-unit fixture.
    from importlib import reload
    real_tools = reload(tools)
    monkeypatch.setattr(activation, "ensure_running", lambda: (_ for _ in ()).throw(RuntimeError("not ready")))
    with pytest.raises(RuntimeError, match="not ready"):
        real_tools._activate("test-only")
    monkeypatch.setattr(tools, "_activate", lambda task_id=None: None)
