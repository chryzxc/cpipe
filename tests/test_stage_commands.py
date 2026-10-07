"""Stage entry uses actual workflow handlers, never a direct-assistant bypass."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cpipe import intents, settings, tools
from test_intents import Context, token


def test_stage_commands_registered():
    assert {"cp-review", "cp-fix", "cp-verify"} <= set(intents.POLICIES)


@pytest.mark.parametrize("command,action", [("cp-review", "review"), ("cp-verify", "verify"),
                                          ("cp-fix", "fix"), ("cp-ship", "ship")])
def test_stage_intent_runs_specialist_backend(monkeypatch, tmp_path, command, action):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(intents, "_session_key", lambda: "stage-session")
    seen = []
    monkeypatch.setattr(tools, "stage", lambda name, args: seen.append((name, args)) or {"task_id": "t_stage"})
    ctx = Context()
    intents.dispatch(ctx, command, "this existing change")
    result = json.loads(intents.execute({"token": token(ctx), "operation": action,
                                         "arguments": {"request": "Review the existing change"}}))
    assert result["task_id"] == "t_stage"
    assert seen == [(action, {"request": "Review the existing change"})]


def test_direct_bypass_no_longer_allowed(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(intents, "_session_key", lambda: "stage-session")
    ctx = Context()
    intents.dispatch(ctx, "cp-ship", "the work we already built")
    assert "error" in json.loads(intents.execute({"token": token(ctx), "operation": "direct", "arguments": {}}))


def git(repo, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def existing(tmp_path, monkeypatch):
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "code.py").write_text("value = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "chore: baseline")
    git(repo, "checkout", "-qb", "feature/existing")
    (repo / "code.py").write_text("value = 2\n")
    git(repo, "commit", "-qam", "feat: existing work")
    monkeypatch.setattr(tools, "resolve_project", lambda *a: ("app", repo))
    monkeypatch.setattr(tools.workspace_prep, "projects", lambda: {"app": {"base_branch": "main"}})
    cfg = settings.load({})
    monkeypatch.setattr(tools.settings, "load", lambda: cfg)
    return repo


def test_adoption_rejects_uncommitted_work(existing):
    (existing / "code.py").write_text("value = 3\n")
    result = tools.stage("review", {"request": "review this"})
    assert "commit" in result["error"].lower()
    assert git(existing, "branch", "--show-current") == "feature/existing"


def test_adoption_rejects_missing_base(existing):
    result = tools.stage("verify", {"request": "verify this", "base": "not-a-base"})
    assert "base" in result["error"].lower()


def test_adoption_rejects_empty_diff(existing):
    git(existing, "checkout", "-q", "main")
    result = tools.stage("review", {"request": "review this"})
    assert "change" in result["error"].lower()


def test_adoption_preserves_source_and_records_provenance(existing, monkeypatch):
    before = git(existing, "rev-parse", "HEAD")
    created, comments = [], []
    class Connection:
        def close(self): pass
    monkeypatch.setattr(tools.hermes_api, "connect", lambda *a: Connection())
    monkeypatch.setattr(tools.hermes_api, "create_card", lambda c, **kw:
                        created.append(kw) or "t_stage")
    monkeypatch.setattr(tools.hermes_api, "comment", lambda c, tid, body, author:
                        comments.append((author, body)))
    result = tools.stage("review", {"request": "review the existing work"})
    assert result["task_id"] == "t_stage"
    assert created[0]["repo"] == str(existing)
    adopted = created[0]["adoption"]
    assert adopted["head"] == before and adopted["base"] == "main"
    assert adopted["entry"] == "review" and not adopted["publish"]
    assert git(existing, "rev-parse", created[0]["branch"]) == before
    assert git(existing, "branch", "--show-current") == "feature/existing"
    assert not git(existing, "status", "--porcelain")


def test_stage_rejects_ambiguous_or_invalid_target():
    assert "error" in tools.stage("review", {"task_id": "bad"})
    assert "error" in tools.stage("unknown", {})
