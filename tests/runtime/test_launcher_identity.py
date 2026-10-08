"""Regression coverage for bootstrap loss and legacy round assignees."""
import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("hermes_cli.kanban_db")
from cpipe import settings
from cpipe.runtime import scheduler
from cpipe.runtime.adapters.round import RoundAdapter
from cpipe.runtime.config import Lane, RuntimeConfig, parse_config
from cpipe.runtime.adapters.command import CommandAdapter

def default_config(cfg):
    return RuntimeConfig(lanes={"default": Lane("hermes", "default", "round")})


def test_round_child_uses_hermes_bootstrap(tmp_path, monkeypatch):
    calls = []
    from hermes_cli import _launchers
    monkeypatch.setattr(_launchers, "runtime_command", lambda root, **kw:
                        calls.append((root, kw)) or ["managed-python", "-c", kw["code"], *kw["args"]])
    lane = default_config(settings.load({})).lanes["default"]
    ctx = SimpleNamespace(task_id="t_test", workspace=tmp_path, env={}, lane=lane)
    monkeypatch.setattr(CommandAdapter, "launch", lambda *a: None)
    adapter = RoundAdapter(lane)
    adapter.launch(ctx)
    try:
        assert adapter.lane.command[0] == "managed-python"
        assert "runpy.run_module('cpipe.round'" in calls[0][1]["code"]
        assert calls[0][1]["args"] == ("t_test", "--result", str(adapter._result))
        assert calls[0][1]["home"]
    finally:
        adapter._result.unlink(missing_ok=True)


def test_supervisor_uses_hermes_bootstrap(monkeypatch):
    lane = default_config(settings.load({})).lanes["default"]
    task = SimpleNamespace(id="t_test", claim_run_id=1)
    calls = []
    monkeypatch.setattr(scheduler.subprocess, "Popen", lambda argv, **kw: calls.append(argv) or SimpleNamespace(pid=123))
    monkeypatch.setattr(scheduler.h, "record_worker_pid", lambda *a: True)
    monkeypatch.setattr(scheduler.h, "heartbeat_worker", lambda *a, **kw: True)
    monkeypatch.setattr(scheduler.h, "worker_log_path", lambda *a, **kw: "/dev/null")
    scheduler.Scheduler(default_config(settings.load({})))._spawn("default", "t_test", 1, "claim", lane)
    assert calls[0][1] == "-c"
    assert "runpy.run_module('cpipe.runtime.supervisor'" in calls[0][2]
    assert "--task" in calls[0] and "t_test" in calls[0]


@pytest.mark.parametrize("node,expected", [("setup", "archon"), ("plan", "archon"), ("tests", "forge"), ("review", "sentry"), ("qa", "sentinel")])
def test_round_assignee_follows_persisted_route(monkeypatch, node, expected):
    cfg = settings.load({})
    route = {"mode": "specialists", "engine": "hermes", "roles": {"plan": "planner", "build": "hermes", "review": "reviewer", "qa": "tester"}, "engines": {
        "hermes": {"kind": "hermes", "profile": "forge"},
        "planner": {"kind": "hermes", "profile": "archon"},
        "reviewer": {"kind": "hermes", "profile": "sentry"},
        "tester": {"kind": "hermes", "profile": "sentinel"}}, "review_kind": "specialist review"}
    task = SimpleNamespace(id="t_test", assignee="cp:hermes", status="ready")
    monkeypatch.setattr("cpipe.settings.load", lambda: cfg)
    monkeypatch.setattr("cpipe.report.state_of", lambda *a: {"node": node, "size": "large", "routing": route, "engine": "hermes"})
    lane = default_config(cfg).lanes["default"]
    actual = scheduler.round_owner(None, task, lane)
    assert actual == expected


def test_external_lane_is_not_reassigned():
    lane = replace(default_config(settings.load({})).lanes["default"], adapter="cli", assignee="custom-cli")
    assert scheduler.round_owner(None, SimpleNamespace(assignee="custom-cli"), lane) == "custom-cli"


def test_round_infrastructure_failures_use_bounded_hermes_retry():
    cfg = parse_config(settings.runtime_raw(settings.load({})))
    assert all(lane.on_failure == "fail" for lane in cfg.lanes.values())


def test_launcher_fails_closed_if_hermes_is_unavailable(monkeypatch):
    from cpipe.runtime import launcher
    monkeypatch.setitem(sys.modules, "hermes_cli._launchers", None)
    with pytest.raises(RuntimeError, match="no supported runtime bootstrap"):
        launcher.module_command("cpipe.round")
