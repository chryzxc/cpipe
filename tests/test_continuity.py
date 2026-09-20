import json
import runpy
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER_PATH = ROOT / "workflow" / "scripts" / "kanban-supervisor-scan.py"


def scanner(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes"
    (hermes_home / "logs").mkdir(parents=True)
    conn = sqlite3.connect(hermes_home / "kanban.db")
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT, title TEXT, status TEXT, assignee TEXT, skills TEXT,
            body TEXT, created_at REAL, started_at REAL, last_heartbeat_at REAL,
            completed_at REAL, workspace_path TEXT, last_failure_error TEXT
        );
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, body TEXT, created_at REAL);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at REAL);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, outcome TEXT, ended_at REAL, claim_expires REAL);
        CREATE TABLE kanban_notify_subs (
            task_id TEXT NOT NULL, platform TEXT NOT NULL, chat_id TEXT NOT NULL,
            delivery_mode TEXT NOT NULL DEFAULT 'notify', created_at INTEGER NOT NULL,
            PRIMARY KEY (task_id, platform, chat_id)
        );
    """)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return conn


def add_card(conn, tid, status, title="Continuity card", completed_at=None):
    conn.execute(
        "INSERT INTO tasks (id, title, status, assignee, skills, body, created_at)"
        " VALUES (?, ?, ?, 'forge', '[]', 'BUDGET: token_budget 20m', ?)",
        (tid, title, status, time.time() - 7200))
    if completed_at is not None:
        conn.execute("UPDATE tasks SET completed_at=? WHERE id=?", (completed_at, tid))


def add_comment(conn, tid, body, created_at=None):
    conn.execute(
        "INSERT INTO task_comments (task_id, body, created_at) VALUES (?, ?, ?)",
        (tid, body, created_at or time.time()))


def subscribe_wake(conn, tid):
    conn.execute(
        "INSERT INTO kanban_notify_subs (task_id, platform, chat_id, delivery_mode, created_at)"
        " VALUES (?, 'tui', 'sess1', 'notify+wake', ?)", (tid, time.time()))


def run_scan(monkeypatch, capsys):
    module = runpy.run_path(str(SCANNER_PATH))
    module["main"]()
    return capsys.readouterr().out


def test_orphaned_chain_flagged_within_window(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "orphan", "done", completed_at=time.time() - 1800)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "ORPHANED_CHAIN · orphan · done without a recorded continuation decision" in output
    conn.close()


def test_continuation_marker_clears_the_signal(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    done_at = time.time() - 1800
    add_card(conn, "linked", "done", completed_at=done_at)
    add_comment(
        conn, "linked",
        "CONTINUATION: successor t_next · evidence: rev-001 approved",
        created_at=done_at + 60)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "ORPHANED_CHAIN · linked" not in output
    conn.close()


def test_marker_before_completion_does_not_count(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    done_at = time.time() - 1800
    add_card(conn, "stale_marker", "done", completed_at=done_at)
    add_comment(
        conn, "stale_marker",
        "CONTINUATION: final report · evidence: rev-002",
        created_at=done_at - 3600)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "ORPHANED_CHAIN · stale_marker" in output
    conn.close()


def test_old_done_cards_left_to_digest(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ancient", "done", completed_at=time.time() - 7 * 86400)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "ORPHANED_CHAIN · ancient" not in output
    conn.close()


def test_running_and_blocked_cards_exempt(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "busy", "running")
    add_card(conn, "parked", "blocked")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "ORPHANED_CHAIN" not in output
    conn.close()


def test_unsubscribed_block_escalation_after_grace(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "waiting", "ready")
    add_comment(
        conn, "waiting", "unsubscribed_card: coordinator waking",
        created_at=time.time() - 45 * 60)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "UNSUBSCRIBED_BLOCK · waiting · ready and unattended for 45m" in output
    conn.close()


def test_unsubscribed_block_silent_within_grace(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "fresh", "ready")
    add_comment(
        conn, "fresh", "unsubscribed_card: coordinator waking",
        created_at=time.time() - 300)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "UNSUBSCRIBED_BLOCK" not in output
    assert "UNSUBSCRIBED_CARD · fresh" not in output
    conn.close()


def test_unsubscribed_block_never_hits_running(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "worker", "running")
    add_comment(
        conn, "worker", "unsubscribed_card: coordinator waking",
        created_at=time.time() - 45 * 60)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "UNSUBSCRIBED_BLOCK" not in output
    conn.close()


def test_idle_board_classified_as_done_with_orphans(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "orphan", "done", completed_at=time.time() - 1800)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "IDLE_BOARD · done-with-orphans" in output
    conn.close()


def test_idle_board_classified_as_awaiting_decisions(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "parked", "blocked")
    conn.execute(
        "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'blocked', ?, ?)",
        ("parked", json.dumps({"reason": "needs_assistance: choose vendor"}), time.time()))
    old = time.time() - 3600
    conn.execute("UPDATE tasks SET created_at=? WHERE id='parked'", (old,))
    conn.execute("UPDATE task_events SET created_at=? WHERE task_id='parked'", (old,))
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "IDLE_BOARD · awaiting decisions (1 blocked)" in output
    conn.close()


def test_busy_board_has_no_idle_classification(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "worker", "running")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "IDLE_BOARD" not in output
    conn.close()


def test_pr_pending_flagged_without_pr_reference(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    done_at = time.time() - 1800
    add_card(conn, "reviewed", "done", completed_at=done_at)
    add_comment(
        conn, "reviewed",
        "CONTINUATION: final report · evidence: rev-003 approved",
        created_at=done_at + 60)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "PR_PENDING · reviewed · reviewed implementation without a PR successor" in output
    conn.close()


def test_pr_pending_silent_with_pr_reference(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    done_at = time.time() - 1800
    add_card(conn, "shipped", "done", completed_at=done_at)
    add_comment(
        conn, "shipped",
        "CONTINUATION: final report · evidence: github.com/chryzxc/repo/pull/12 merged",
        created_at=done_at + 60)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "PR_PENDING · shipped" not in output
    conn.close()
