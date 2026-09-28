"""The operator must hear when delivery stops moving, once, and hear nothing while it moves."""
import runpy
import sqlite3
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "stall_alert.py"


def board(tmp_path):
    conn = sqlite3.connect(tmp_path / "kanban.db")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE tasks (id TEXT, title TEXT, assignee TEXT, status TEXT, block_kind TEXT,
            consecutive_failures INTEGER DEFAULT 0, last_failure_error TEXT, result TEXT,
            last_heartbeat_at REAL, created_at REAL);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT,
            created_at REAL);
    """)
    return conn


def card(conn, tid, status, minutes_ago, kind=None, reason=None):
    at = time.time() - minutes_ago * 60
    conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,0,NULL,NULL,?,?)",
                 (tid, f"Card {tid}", "forge", status, kind, at, at))
    conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                 (tid, "blocked" if status == "blocked" else "claimed",
                  f'{{"reason": "{reason or ""}"}}', at))
    conn.commit()


def digest(conn, state):
    mod = runpy.run_path(str(SCRIPT))
    now = time.time()
    current = {**mod["engine_stalls"](conn, now), **mod["card_stalls"](conn, now)}
    return mod["build_digest"](current, state, now)


def test_new_stall_alerts_once_then_resolution_is_reported(tmp_path):
    conn = board(tmp_path)
    card(conn, "t_wait", "blocked", 45, "needs_input", "Pick option A or B")
    card(conn, "t_fresh", "blocked", 5, "needs_input")        # inside grace window
    card(conn, "t_busy", "running", 2)                        # healthy worker

    message, state = digest(conn, {})
    assert "t_wait" in message and "waiting on YOUR decision" in message
    assert "Pick option A or B" in message
    assert "t_fresh" not in message and "t_busy" not in message

    assert digest(conn, state)[0] == ""                        # no repeat while unchanged

    conn.execute("UPDATE tasks SET status='running' WHERE id='t_wait'")
    conn.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_wait','claimed',?)",
                 (time.time(),))
    conn.commit()
    message, state = digest(conn, state)
    assert "Moving again: Card t_wait" in message


def test_engine_crash_storm_is_one_board_level_alert(tmp_path):
    conn = board(tmp_path)
    for _ in range(3):
        conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES "
                     "('t_x','crashed','{\"worker_output\": \"No module hermes_cli.main\"}',?)",
                     (time.time(),))
    conn.commit()
    message, _ = digest(conn, {})
    assert "Workers are crashing on start" in message and "hermes_cli.main" in message


def test_silent_worker_and_unclaimed_ready_card_are_stalls(tmp_path):
    conn = board(tmp_path)
    card(conn, "t_ready", "ready", 30)
    card(conn, "t_silent", "running", 60)
    message, _ = digest(conn, {})
    assert "t_ready" in message and "no worker picked it up" in message
    assert "t_silent" in message and "gone silent" in message


def test_digest_fits_a_chat_message(tmp_path):
    conn = board(tmp_path)
    for i in range(60):
        card(conn, f"t_{i}", "blocked", 60 + i, "capability", "x" * 300)
    message, _ = digest(conn, {})
    assert len(message) < 2000 and "more —" in message
