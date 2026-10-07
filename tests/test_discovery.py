"""Optional discovery: bounded source snapshots, isolated calls and revision-scoped reuse."""
import subprocess

import pytest

from cpipe import engines, settings
from cpipe.engines import hermes
from test_profiled_workflow import runner


def repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "routes.py").write_text("def save_order():\n    return persist_order()\n")
    (path / "test_orders.py").write_text("def test_save_order():\n    assert True\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture"], check=True)


def configured():
    return settings.load({"default_engine": "builder", "engines": {
        "builder": {"kind": "hermes", "profile": "example-builder"},
        "explorer": {"kind": "hermes", "profile": "example-explorer"}},
        "roles": {"discover": "explorer"}})


def discovery_runner(tmp_path, monkeypatch):
    repo(tmp_path)
    r = runner(tmp_path, monkeypatch, configured())
    r.task.title = "Find order persistence and callers"
    r.state["size"] = "large"
    return r


def test_discover_role_is_optional_and_validated():
    assert settings.load({})["roles"] == {}
    assert configured()["roles"]["discover"] == "explorer"
    with pytest.raises(ValueError):
        settings.load({"roles": {"discover": "missing"}})


@pytest.mark.parametrize("size", ["small", "content"])
def test_fast_paths_skip_discovery(tmp_path, monkeypatch, size):
    r = discovery_runner(tmp_path, monkeypatch)
    r.state["size"] = size
    monkeypatch.setattr(hermes, "run", lambda *a, **kw: pytest.fail("unnecessary discovery"))
    r.discover()
    assert not r.state.get("discovery", {}).get("brief")


def test_bounded_readonly_discovery_reuses_and_invalidates_same_status_change(tmp_path, monkeypatch):
    r = discovery_runner(tmp_path, monkeypatch)
    calls = []
    def run(ec, prompt, cwd, **kw):
        calls.append((ec, prompt, kw))
        return engines.EngineResult("done", data={"brief": "Trace routes.py:1 and test_orders.py:1; persistence unresolved."},
                                    tokens={"input_tokens": 20, "output_tokens": 10}, session="explorer-session")
    monkeypatch.setattr(hermes, "run", run)
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    r.discover()
    r.discover()
    assert len(calls) == 1
    assert calls[0][2]["readonly"] is True and calls[0][2]["session"] is None
    assert calls[0][0]["discovery_only"] is True
    assert "routes.py:1" in calls[0][1]
    assert len(calls[0][1]) < 24000
    assert r.state["by_node"]["discover"]["input_tokens"] == 20
    assert r.state["agent_calls"][0]["node"] == "discover"
    assert not r.state["sessions"]
    (tmp_path / "routes.py").write_text("def save_order():\n    return other_store()\n")
    r.discover()
    (tmp_path / "routes.py").write_text("def save_order():\n    return third_store()\n")
    r.discover()
    assert len(calls) == 3


@pytest.mark.parametrize("status", ["failed", "rate_limited", "unavailable"])
def test_discovery_failure_is_nonblocking_and_not_retried_on_same_revision(tmp_path, monkeypatch, status):
    r = discovery_runner(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    monkeypatch.setattr(hermes, "run", lambda *a, **kw: calls.append(1) or engines.EngineResult(status))
    r.discover()
    r.discover()
    assert calls == [1]
    assert r.state["discovery"]["status"] == status
    assert not r.state["discovery"].get("brief")


def test_snapshot_does_not_read_ignored_secrets_or_follow_symlinks(tmp_path, monkeypatch):
    from cpipe import discovery
    repo(tmp_path)
    outside = tmp_path.parent / "secret.py"
    outside.write_text("PRIVATE_SENTINEL")
    (tmp_path / "link.py").symlink_to(outside)
    (tmp_path / ".env").write_text("PRIVATE_SENTINEL")
    (tmp_path / "ignored.py").write_text("PRIVATE_SENTINEL")
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    snapshot, _ = discovery.snapshot(tmp_path, "order")
    assert "PRIVATE_SENTINEL" not in snapshot
    assert "routes.py:1" in snapshot


def test_hermes_discovery_command_disables_tools_and_bounds_run(tmp_path):
    argv = hermes.command({"profile": "example-explorer", "discovery_only": True}, "map", tmp_path, None)
    assert argv[argv.index("--toolsets") + 1] == "none"
    assert "--ignore-rules" in argv
    assert argv[argv.index("--run-budget") + 1] == "60"
    assert "--yolo" not in argv and "--resume" not in argv


def test_setup_hands_brief_to_builder_without_write_instructions_for_explorer(tmp_path, monkeypatch):
    r = discovery_runner(tmp_path, monkeypatch)
    prompts = []
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    def run(ec, prompt, cwd, **kw):
        prompts.append(prompt)
        return engines.EngineResult("done", data={"brief": "Relevant: routes.py:1; tests: test_orders.py:1."})
    monkeypatch.setattr(hermes, "run", run)
    outcome, prompt = r.setup("", "")
    assert outcome == "ok"
    assert "Relevant: routes.py:1" in prompt
    assert "commit (Conventional Commits)" not in prompts[0]
    assert "not verification" in prompt


def test_unconfigured_and_non_hermes_roles_do_not_call_agents(tmp_path, monkeypatch):
    r = discovery_runner(tmp_path, monkeypatch)
    monkeypatch.setattr(hermes, "run", lambda *a, **kw: pytest.fail("unexpected agent call"))
    r.cfg["roles"].pop("discover")
    r.discover()
    assert "discovery" not in r.state
    r.cfg["roles"]["discover"] = "explorer"
    r.cfg["engines"]["explorer"]["kind"] = "codex"
    r.discover()
    assert r.state["discovery"]["status"] == "unavailable"


def test_startup_failure_is_cached_and_has_no_builder_fallback(tmp_path, monkeypatch):
    r = discovery_runner(tmp_path, monkeypatch)
    r.cfg["fallback_engine"] = "builder"
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise OSError("startup failure")
    monkeypatch.setattr(hermes, "run", fail)
    r.discover()
    r.discover()
    assert calls == [1]
    assert r.state["discovery"]["status"] == "unavailable"
    assert r.state["engine"] == "builder"


def test_request_untracked_content_and_engine_config_invalidate_brief(tmp_path, monkeypatch):
    r = discovery_runner(tmp_path, monkeypatch)
    calls = []
    def run(ec, *args, **kwargs):
        calls.append(ec)
        return engines.EngineResult("done", data={"brief": "x" * 5000})
    monkeypatch.setattr(hermes, "run", run)
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    r.state["model"] = "builder-override"
    r.discover()
    assert len(r.state["discovery"]["brief"]) == 3000
    assert "model" not in calls[0]
    assert calls[0]["profile"] == "example-explorer"
    (tmp_path / "new.py").write_text("def new_store(): pass")
    r.discover()
    (tmp_path / "new.py").write_text("def old_store(): pass")
    r.discover()
    r.task.title += " and errors"
    r.discover()
    r.cfg["engines"]["explorer"]["model"] = "explicit-explorer-model"
    r.discover()
    assert len(calls) == 5


def test_oversized_inventory_falls_back_without_agent_call(tmp_path, monkeypatch):
    from cpipe import discovery
    r = discovery_runner(tmp_path, monkeypatch)
    monkeypatch.setattr(discovery, "MAX_FILES", 1)
    monkeypatch.setattr(hermes, "run", lambda *a, **kw: pytest.fail("unbounded discovery"))
    r.discover()
    assert r.state["discovery"]["status"] == "unavailable"


def test_discovery_hard_timeout_kills_and_reaps_process(tmp_path, monkeypatch):
    class Process:
        killed = False
        calls = 0
        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                assert timeout == 75
                raise subprocess.TimeoutExpired("hermes", 75)
            return "", None
        def kill(self):
            self.killed = True
    proc = Process()
    monkeypatch.setattr(hermes.subprocess, "Popen", lambda *a, **kw: proc)
    result = hermes.run({"discovery_only": True}, "map", tmp_path, schema={"properties": {}})
    assert result.status == "failed"
    assert proc.killed and proc.calls == 2
