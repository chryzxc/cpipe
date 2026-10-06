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
        CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, assignee TEXT, block_kind TEXT, created_at REAL,
                            body TEXT);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at REAL);
        CREATE TABLE task_links (parent_id TEXT, child_id TEXT);
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT);
    """)
    return conn


def card(conn, tid, status, kind=None, reason=None, parent=None, event="blocked", body="", age=3600):
    at = time.time() - age
    conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)", (tid, f"Card {tid}", status, "forge", kind, at, body))
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
    mod["auto_fix"](cards, state, walled=False, run=run)
    assert "t_quota_triage" not in calls and "t_merged" not in calls     # the operator decides those

    now = time.time()
    message, _ = mod["build_digest"](cards, [], [], {}, now)
    assert "t_ask" in message and "Pick option A or B" in message and "t_quota_triage" in message
    assert "`t_quota`" not in message                                       # retries itself, no nag

    quota = next(c for c in cards if c["id"] == "t_quota")
    assert calls.count("t_quota") == 1
    mod["auto_fix"](cards, state, walled=False, run=run)                   # the retry failed the same way
    assert calls.count("t_quota") == 1 and quota["group"] == "decide"    # → the operator, not a loop
    quota.update(reason="request timed out", group="retry")               # a new error is worth a retry
    mod["auto_fix"](cards, state, walled=False, run=run)
    assert calls.count("t_quota") == mod["MAX_RETRIES"]
    for c in cards:
        if c["id"] == "t_quota":
            c.update(reason="request timed out", group="retry")
    mod["auto_fix"](cards, state, walled=False, run=run)
    assert calls.count("t_quota") == mod["MAX_RETRIES"]                    # bounded, never a loop

    message, state = mod["build_digest"](cards, [], [], state, now)
    assert len(message) <= mod["MESSAGE_BUDGET"]
    assert mod["build_digest"](cards, [], [], state, now + 3600)[0] == ""  # quiet until tomorrow
    card(conn, "t_new_ask", "blocked", "needs_input", "Approve the schema change?")
    assert "t_new_ask" in mod["build_digest"](mod["classify"](conn), [], [], state, now + 3600)[0]


def test_queue_lists_non_empty_groups_in_order(tmp_path):
    mod = runpy.run_path(str(SCRIPT))
    conn = board(tmp_path)
    card(conn, "t_env", "blocked", "capability", "workspace is not a git repo")
    card(conn, "t_ask", "blocked", "needs_input", "Pick option A or B")

    q = mod["queue"](conn)
    assert [g["key"] for g in q["groups"]] == ["decide", "stale"]
    assert q["total"] == 2 and q["provider_walled"] is False
    assert q["groups"][0]["cards"][0]["reason"] == "Pick option A or B"


def note(conn, tid, body):
    conn.execute("INSERT INTO task_comments (task_id, body) VALUES (?,?)", (tid, body))
    conn.commit()


def test_finished_cards_are_archived_and_ignored_ones_warned_first(tmp_path):
    mod = runpy.run_path(str(SCRIPT))
    conn = board(tmp_path)
    card(conn, "t_a1", "blocked", "needs_input", "wait")
    card(conn, "t_5cc", "todo")
    note(conn, "t_a1", "CONTINUATION: successor t_5cc (owner forge) took over")
    card(conn, "t_a2", "triage", None, "wait")
    note(conn, "t_a2", "CONTINUATION: stop — Christian dropped it")
    card(conn, "t_a3", "blocked", "needs_input", "wait")                 # moved on, but its child still waits
    note(conn, "t_a3", "CONTINUATION: superseded by t_5cc")
    card(conn, "t_a3c", "todo", parent="t_a3")
    card(conn, "t_b1", "blocked", "needs_input", "check iOS")            # the delivery's PR is merged
    note(conn, "t_b1", "draft PR https://github.com/o/r/pull/7")
    card(conn, "t_b2", "blocked", "needs_input", "retest", body="DELIVERY: t_b1")
    card(conn, "t_b2c", "todo", parent="t_b2", body="DELIVERY: t_b1")
    card(conn, "t_c1", "blocked", "needs_input", "check", body="DELIVERY: t_c0")  # merged, child running
    card(conn, "t_c0", "done")
    note(conn, "t_c0", "https://github.com/o/r/pull/8")
    card(conn, "t_c1c", "running", parent="t_c1")
    card(conn, "t_d1", "blocked", "needs_input", "PR still open")
    note(conn, "t_d1", "https://github.com/o/r/pull/9")
    card(conn, "t_old", "triage", None, "pick A or B", age=8 * 86400)
    card(conn, "t_new", "triage", None, "pick A or B")

    prs = {"7": "MERGED", "8": "MERGED", "9": "OPEN"}
    gh = lambda o, r, n: prs[n]
    now, state = time.time(), {}
    done = mod["finished"](conn, mod["classify"](conn, now), now, state, prs=gh)
    assert done == {"t_a1": ("moved to t_5cc", ["t_a1"]), "t_a2": ("stopped", ["t_a2"]),
                    "t_b1": ("PR merged", ["t_b1"]), "t_b2": ("PR merged", ["t_b2", "t_b2c"])}
    assert state["ignored"] == {"t_old": now}                            # warned, not archived yet

    calls = []
    archived = mod["auto_archive"](done, state, run=lambda *a: (calls.append(a), (True, ""))[1])
    assert archived == ["t_a1", "t_a2", "t_b1", "t_b2", "t_b2c"] and all(c[:2] == ("kanban", "archive") for c in calls)
    left = [c for c in mod["classify"](conn, now) if c["id"] not in archived]
    message, state = mod["build_digest"](left, [], [], state, now, archived)
    assert "archived 5 finished" in message and "archived in 24h unless you reply: t_old" in message

    later = now + 3600
    assert "t_old" not in mod["finished"](conn, left, later, state, prs=gh)   # still inside the 24h warning
    assert mod["finished"](conn, left, now + 86400, state, prs=gh)["t_old"][1] == ["t_old"]
