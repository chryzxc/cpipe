"""Stuck cards land in the right group, only safe ones move by themselves, and the reminder is daily."""
import json
import runpy
import sqlite3
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "board_triage.py"


def board(tmp_path):
    conn = sqlite3.connect(tmp_path / "kanban.db")
    conn.executescript("""
        CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, assignee TEXT, block_kind TEXT, created_at REAL);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at REAL);
        CREATE TABLE task_links (parent_id TEXT, child_id TEXT);
    """)
    return conn


def card(conn, tid, status, kind=None, reason=None, parent=None, event="blocked"):
    at = time.time() - 3600
    conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)", (tid, f"Card {tid}", status, "forge", kind, at))
    if reason is not None:
        conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                     (tid, event, json.dumps({"reason": reason}), at))
    if parent:
        conn.execute("INSERT INTO task_links VALUES (?,?)", (parent, tid))
    conn.commit()


def test_groups_auto_fixes_and_reminds_once_a_day(tmp_path):
    mod = runpy.run_path(str(SCRIPT))
    conn = board(tmp_path)
    card(conn, "t_parent", "running")
    card(conn, "t_quota", "blocked", "transient", "429 Too Many Requests")
    card(conn, "t_quota_triage", "triage", None, "judge error: RateLimitError", event="block_loop_detected")
    card(conn, "t_merged", "blocked", "needs_input", "Change is already merged in PR #12")
    card(conn, "t_env", "blocked", "capability", "workspace is not a git repo")
    card(conn, "t_ask", "blocked", "needs_input", "Pick option A or B")
    card(conn, "t_child", "blocked", parent="t_parent")
    card(conn, "t_parked", "blocked")

    groups = {c["id"]: c["group"] for c in mod["classify"](conn)}
    assert groups == {"t_quota": "retry", "t_quota_triage": "retry", "t_merged": "done", "t_env": "stale",
                      "t_ask": "decide", "t_child": "waiting", "t_parked": "parked"}

    calls, state = [], {}
    run = lambda *a: (calls.append(a[2]) or (True, ""))
    cards = mod["classify"](conn)
    assert mod["auto_fix"](cards, state, walled=True, run=run) == ([], ["t_child"])  # provider still walled
    for _ in range(3):
        mod["auto_fix"](cards, state, walled=False, run=run)
    assert calls.count("t_quota") == mod["MAX_RETRIES"]                    # bounded, never a loop
    assert "t_quota_triage" not in calls and "t_merged" not in calls     # the operator decides those

    now = time.time()
    message, state = mod["build_digest"](cards, [], [], state, now)
    assert "t_ask" in message and "Pick option A or B" in message and "t_quota_triage" in message
    assert "`t_quota`" not in message                                       # retries itself, no nag
    assert len(message) <= mod["MESSAGE_BUDGET"]
    assert mod["build_digest"](cards, [], [], state, now + 3600)[0] == ""  # quiet until tomorrow
    card(conn, "t_new_ask", "blocked", "needs_input", "Approve the schema change?")
    assert "t_new_ask" in mod["build_digest"](mod["classify"](conn), [], [], state, now + 3600)[0]
