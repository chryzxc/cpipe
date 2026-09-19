import json
import os
import runpy
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from software_delivery import _mutation_check


ROOT = Path(__file__).resolve().parents[1]
SCANNER_PATH = ROOT / "workflow" / "scripts" / "kanban-supervisor-scan.py"
WARM_BUILD_PATH = ROOT / "workflow" / "scripts" / "warm_build_scan.py"


def scanner_module():
    return runpy.run_path(str(SCANNER_PATH))


def board(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT, title TEXT, status TEXT, assignee TEXT, skills TEXT,
            created_at REAL, started_at REAL, last_heartbeat_at REAL,
            workspace_path TEXT, last_failure_error TEXT
        );
        CREATE TABLE task_comments (task_id TEXT, body TEXT, created_at REAL);
        CREATE TABLE task_events (task_id TEXT, kind TEXT, payload TEXT, created_at REAL);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, ended_at REAL, claim_expires REAL);
    """)
    return conn


def test_workspace_must_be_git_root(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "src"
    nested.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    is_git_workspace = scanner_module()["_is_git_workspace"]

    assert is_git_workspace(str(repo))
    assert not is_git_workspace(str(nested))


def test_workspace_check_handles_git_timeout(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    module = scanner_module()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(module["subprocess"], "run", timeout)

    assert not module["_is_git_workspace"](str(workspace))


def test_scanner_reports_undispatched_review_and_missing_heartbeat(tmp_path, monkeypatch, capsys):
    now = time.time()
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    db = hermes_home / "kanban.db"
    conn = board(db)
    conn.executemany(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("review-1", "Review change", "review", "sentry", "[]", now - 3 * 3600,
             None, None, None, None),
            ("running-1", "Implement change", "running", "forge", "[]", now - 600,
             now - 600, None, None, None),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module = scanner_module()

    module["main"]()

    output = capsys.readouterr().out
    assert "REVIEW_STALLED · review-1" in output
    assert "HEARTBEAT_STALE · running-1" in output


def test_current_workspace_wins_over_stale_error(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    db = hermes_home / "kanban.db"
    conn = board(db)
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("ready-1", "Ready task", "ready", "forge", "[]", time.time(), None, None,
         str(workspace), "workspace: not inside a git repo"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module = scanner_module()

    module["main"]()

    assert "WORKSPACE_INVALID" not in capsys.readouterr().out


def test_mutation_check_rejects_target_outside_worktree(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").mkdir()
    outside = tmp_path / "outside.swift"
    outside.write_text("value == other")

    result = json.loads(_mutation_check(str(worktree), "../outside.swift", "TargetTests"))

    assert result == {"ok": False, "error": "file_path must stay inside the worktree"}
    assert outside.read_text() == "value == other"


def test_warm_build_launches_without_shell(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    workspace = tmp_path / "work tree; unsafe"
    workspace.mkdir()
    (workspace / "Package.swift").write_text("// package")
    (hermes_home / "kanban" / "logs").mkdir(parents=True)
    conn = board(hermes_home / "kanban.db")
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("warm-1", "Warm build", "todo", "forge", "[]", time.time(), None, None,
         str(workspace), None),
    )
    conn.commit()
    conn.close()
    launched = []

    def fake_popen(command, **kwargs):
        launched.append((command, kwargs))

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    runpy.run_path(str(WARM_BUILD_PATH))

    assert launched[0][0][0] == sys.executable
    assert "/bin/bash" not in launched[0][0]
    assert str(workspace) in launched[0][0]
