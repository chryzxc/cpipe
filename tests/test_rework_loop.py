import json
import runpy
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER_PATH = ROOT / "workflow" / "scripts" / "kanban-supervisor-scan.py"
CONFIG = "kanban:\n  max_in_progress: 6\n  max_in_progress_per_profile: 3\n"


def scanner(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir(exist_ok=True)
    (hermes_home / "config.yaml").write_text(CONFIG)
    conn = sqlite3.connect(hermes_home / "kanban.db")
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT, title TEXT, status TEXT, assignee TEXT, skills TEXT,
            body TEXT, created_at REAL, started_at REAL, last_heartbeat_at REAL, completed_at REAL,
            workspace_path TEXT, last_failure_error TEXT
        );
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT, created_at REAL);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at REAL);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, outcome TEXT, ended_at REAL, claim_expires REAL);
    """)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return conn


def add_card(conn, tid, status, title="Thrashing card"):
    conn.execute(
        "INSERT INTO tasks (id, title, status, assignee, skills, body, created_at)"
        " VALUES (?, ?, ?, 'forge', '[]', 'BUDGET: token_budget 20m', ?)",
        (tid, title, status, time.time()))


def add_cycles(conn, tid, count):
    for _ in range(count):
        conn.execute(
            "INSERT INTO task_runs (task_id, outcome, ended_at) VALUES (?, 'changes_requested', ?)",
            (tid, time.time()))


def run_scan(monkeypatch, capsys):
    module = runpy.run_path(str(SCANNER_PATH))
    module["main"]()
    return capsys.readouterr().out


def test_rework_loop_emitted_above_threshold(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "thrash", "review")
    add_cycles(conn, "thrash", 4)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "REWORK_LOOP · thrash · 4 review cycles without completion" in output
    conn.close()


def test_rework_loop_silent_at_or_below_threshold(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "calm", "review")
    add_cycles(conn, "calm", 3)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "REWORK_LOOP" not in output
    conn.close()


def test_rework_loop_skips_blocked_and_done(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "parked", "blocked")
    add_cycles(conn, "parked", 5)
    add_card(conn, "shipped", "done")
    add_cycles(conn, "shipped", 5)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "REWORK_LOOP" not in output
    conn.close()


def test_rework_loop_wake_once_via_comment_dedupe(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "thrash", "review")
    add_cycles(conn, "thrash", 5)
    conn.execute(
        "INSERT INTO task_comments (task_id, body, created_at) VALUES (?, ?, ?)",
        ("thrash", "rework_loop: coordinator reconciling scope", time.time()))
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "REWORK_LOOP" not in output
    conn.close()


def test_supervisor_prompt_carries_rework_rule():
    defs = json.loads((ROOT / "workflow" / "cron.jobs.json").read_text())
    supervisor = next(j for j in defs if j["name"] == "Kanban stall supervisor")
    assert "REWORK_LOOP:" in supervisor["prompt"]
    assert "reconcile" in supervisor["prompt"]


def test_skill_carries_review_cycle_contract():
    skill = (ROOT / "workflow" / "skills" / "my-software-delivery-orchestrator" / "SKILL.md").read_text()
    assert "delta-scoped" in skill
    assert "after 3 cycles" in skill
    reference = (ROOT / "workflow" / "skills" / "my-software-delivery-orchestrator" /
                 "references" / "code-quality-review.md").read_text()
    assert "Pass-one exhaustiveness" in reference
    assert "Delta-scope rework review" in reference
    assert "Cycle escalation" in reference
