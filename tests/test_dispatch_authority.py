import json
import runpy
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER_PATH = ROOT / "workflow" / "scripts" / "kanban-supervisor-scan.py"

INCIDENT_REASON = (
    "DISPATCH_BLOCKED: forge profile is at its active-worker cap; "
    "currently running t_89cab86d and t_a2b27d7c; workspace is valid and dispatch spawned no run."
)
CONFIG = "kanban:\n  max_in_progress: 6\n  max_in_progress_per_profile: 3\n"


def scanner(tmp_path, monkeypatch, config=CONFIG):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir(exist_ok=True)
    if config is not None:
        (hermes_home / "config.yaml").write_text(config)
    conn = sqlite3.connect(hermes_home / "kanban.db")
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT, title TEXT, status TEXT, assignee TEXT, skills TEXT,
            body TEXT, created_at REAL, started_at REAL, last_heartbeat_at REAL,
            workspace_path TEXT, last_failure_error TEXT
        );
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT, created_at REAL);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at REAL);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, outcome TEXT, ended_at REAL, claim_expires REAL);
    """)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return conn, hermes_home


def add_card(conn, tid, status, assignee="forge", body="BUDGET: token_budget 20m"):
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
        (tid, f"Card {tid}", status, assignee, "[]", body, time.time()))


def add_block(conn, tid, reason):
    conn.execute(
        "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'blocked', ?, ?)",
        (tid, json.dumps({"reason": reason, "kind": "capability"}), time.time()))


def run_scan(monkeypatch, capsys):
    module = runpy.run_path(str(SCANNER_PATH))
    module["main"]()
    return capsys.readouterr().out


def test_capacity_hold_emitted_for_incident_pattern(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "victim", "blocked")
    add_block(conn, "victim", INCIDENT_REASON)
    for i in range(2):
        add_card(conn, f"busy_{i}", "running")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "CAPACITY_HOLD · victim · blocked on claimed capacity but 2 running < cap 3" in output
    conn.close()


def test_capacity_hold_silent_when_genuinely_capped(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "victim", "blocked")
    add_block(conn, "victim", INCIDENT_REASON)
    for i in range(3):
        add_card(conn, f"busy_{i}", "running")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "CAPACITY_HOLD" not in output
    conn.close()


def test_capacity_hold_ignores_capability_wording(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "victim", "blocked")
    add_block(conn, "victim", "blocked: capability missing on profile")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "CAPACITY_HOLD" not in output
    conn.close()


def test_capacity_hold_suppressed_without_config(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch, config=None)
    add_card(conn, "victim", "blocked")
    add_block(conn, "victim", INCIDENT_REASON)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "CAPACITY_HOLD" not in output
    conn.close()


def test_capacity_hold_skips_verdict_parked_cards(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "victim", "blocked")
    add_block(conn, "victim", INCIDENT_REASON)
    conn.execute(
        "INSERT INTO task_comments (task_id, body, created_at) VALUES (?, ?, ?)",
        ("victim", "REQUEST_CHANGES: rev-001 fix auth scoping", time.time()))
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "CAPACITY_HOLD" not in output
    conn.close()


def test_card_no_budget_flags_once_then_stays_silent(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "poor", "todo", body="OBJECTIVE: implement thing")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "CARD_NO_BUDGET · poor" in output

    conn.execute(
        "INSERT INTO task_comments (task_id, body, created_at) VALUES (?, ?, ?)",
        ("poor", "please amend the body with an explicit token_budget", time.time()))
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "CARD_NO_BUDGET" not in output
    conn.close()


def test_card_no_budget_ignores_blocked_cards(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "parked", "blocked", body="OBJECTIVE: implement thing")
    add_block(conn, "parked", "needs_input from operator")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "CARD_NO_BUDGET" not in output
    conn.close()


def test_supervisor_prompt_carries_new_rules():
    defs = json.loads((ROOT / "workflow" / "cron.jobs.json").read_text())
    supervisor = next(j for j in defs if j["name"] == "Kanban stall supervisor")
    assert "CAPACITY_HOLD:" in supervisor["prompt"]
    assert "CARD_NO_BUDGET:" in supervisor["prompt"]
    assert "dispatcher owns concurrency" in supervisor["prompt"]


def test_skill_carries_dispatch_authority_rule():
    skill = (ROOT / "workflow" / "skills" / "my-software-delivery-orchestrator" / "SKILL.md").read_text()
    assert "dispatcher alone decides spawning and concurrency" in skill
    assert "skipped_per_profile_capped" in skill
    assert "Never block, hold, or \"park\" a `ready` card for capacity" in skill
