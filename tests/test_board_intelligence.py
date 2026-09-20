import json
import os
import runpy
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEL_PATH = ROOT / "workflow" / "scripts" / "board_intelligence.py"


def intel_module(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes"
    (hermes_home / "logs").mkdir(parents=True)
    conn = sqlite3.connect(hermes_home / "kanban.db")
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT, assignee TEXT, created_at REAL, started_at REAL,
            completed_at REAL, status TEXT
        );
        CREATE TABLE task_runs (
            id INTEGER PRIMARY KEY, task_id TEXT, profile TEXT,
            status TEXT, outcome TEXT, started_at REAL, ended_at REAL
        );
        CREATE TABLE task_comments (task_id TEXT, body TEXT, created_at REAL);
    """)
    conn.commit()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return runpy.run_path(str(INTEL_PATH)), conn


def add_card(conn, tid, created_at):
    conn.execute(
        "INSERT INTO tasks VALUES (?, 'reviewer', ?, NULL, NULL, 'review')",
        (tid, created_at))


def add_changes(conn, tid, body, created_at):
    conn.execute(
        "INSERT INTO task_comments VALUES (?, ?, ?)", (tid, body, created_at))


def test_recurrence_lists_class_at_threshold_and_skips_below(tmp_path, monkeypatch):
    now = time.time()
    module, conn = intel_module(tmp_path, monkeypatch)
    for i in range(3):
        add_card(conn, f"t_{i}", now - 86400)
        add_changes(conn, f"t_{i}", "REQUEST_CHANGES: missing evidence for auth criteria", now - 86400 + i)
    add_card(conn, "t_pair", now - 86400)
    add_changes(conn, "t_pair", "REQUEST_CHANGES: no evidence block attached", now - 86400)
    conn.commit()

    recurrent = module["finding_recurrence"](conn, now)

    assert ("missing-evidence", 4, "t_0") in recurrent
    conn.close()


def test_recurrence_skips_below_threshold_and_old_comments(tmp_path, monkeypatch):
    now = time.time()
    module, conn = intel_module(tmp_path, monkeypatch)
    for i in range(2):
        add_card(conn, f"t_scope_{i}", now - 86400)
        add_changes(conn, f"t_scope_{i}", "REQUEST_CHANGES: scope creep beyond the brief", now - 86400 + i)
    add_card(conn, "t_old", now - 40 * 86400)
    add_changes(
        conn, "t_old",
        "REQUEST_CHANGES: missing evidence for auth criteria", now - 40 * 86400)
    conn.commit()

    assert module["finding_recurrence"](conn, now) == []
    conn.close()


def test_digest_contains_recurrence_section(tmp_path, monkeypatch, capsys):
    now = time.time()
    module, conn = intel_module(tmp_path, monkeypatch)
    for i in range(3):
        add_card(conn, f"t_t_{i}", now - 86400)
        add_changes(
            conn, f"t_t_{i}",
            "REQUEST_CHANGES: timeout on flaky suite, intermittent failure",
            now - 86400 + i)
    conn.commit()
    conn.close()

    module["main"]()

    assert "## Finding-class recurrence (28d)" in capsys.readouterr().out
    digest = (tmp_path / "hermes" / "logs" / "board-intelligence.md").read_text()
    assert "timeout-flake: 3" in digest


def test_reviewer_gate_line_uses_roster_name(tmp_path, monkeypatch, capsys):
    now = time.time()
    module, conn = intel_module(tmp_path, monkeypatch)
    (tmp_path / "hermes" / "roster.yaml").write_text(
        "roles:\n  reviewer: my-review-bot\n")
    conn.execute(
        "INSERT INTO tasks VALUES ('t_x', 'my-review-bot', ?, ?, ?, 'review')",
        (now - 3600, now - 120, now - 60))
    conn.execute(
        "INSERT INTO task_runs (id, task_id, profile, status, outcome, started_at, ended_at)"
        " VALUES (1, 't_x', 'my-review-bot', 'blocked', 'blocked', ?, ?)",
        (now - 60, now - 30))
    conn.commit()
    conn.close()

    module["main"]()

    assert "my-review-bot change-request/verdict runs: 1" in capsys.readouterr().out


def test_changes_word_does_not_classify_as_hang(tmp_path, monkeypatch):
    now = time.time()
    module, conn = intel_module(tmp_path, monkeypatch)
    for i in range(3):
        add_card(conn, f"t_ev_{i}", now - 86400)
        add_changes(
            conn, f"t_ev_{i}",
            "REQUEST_CHANGES: missing evidence for auth criteria",
            now - 86400 + i)
    conn.commit()

    recurrent = module["finding_recurrence"](conn, now)

    assert recurrent == [("missing-evidence", 3, "t_ev_0")]
    conn.close()


def test_cron_learning_loop_rule_present():
    defs = json.loads((ROOT / "workflow" / "cron.jobs.json").read_text())
    job = next(j for j in defs if j["name"] == "Board intelligence weekly")
    assert job["no_agent"] is False
    assert "STANDARDS_AMENDMENT" in job["prompt"] or "Standards amendment draft" in job["prompt"]
    assert "resolve_role.py coordinator" in job["prompt"]
