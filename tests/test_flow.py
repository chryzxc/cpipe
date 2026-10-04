"""A delivery's board events become one structured conversation, each event posted once."""
import json
import sqlite3

from cpipe import flow

WT = "/repos/app/.worktrees/t_b1"


class FakeDB:
    def __init__(self):
        self.sessions, self.titles, self.messages = [], {}, []

    def create_session(self, sid, source):
        self.sessions.append((sid, source))

    def set_session_title(self, sid, title):
        if title in self.titles.values():
            raise ValueError("title in use")
        self.titles[sid] = title

    def append_message(self, sid, role, content, timestamp=None):
        self.messages.append((sid, role, content))


def board():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE tasks (id TEXT, title TEXT, assignee TEXT, workspace_path TEXT, created_by TEXT,
            created_at REAL, body TEXT);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, run_id INTEGER, kind TEXT,
            payload TEXT, created_at REAL);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY, profile TEXT, started_at REAL, summary TEXT);
        CREATE TABLE task_links (parent_id TEXT, child_id TEXT);
    """)
    conn.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)", [
        ("t_b1", "Add export", "builder", WT, "delivery_submit", 1, ""),
        ("t_v1", "Verify: Add export", "checker", WT, "delivery_submit", 2, ""),
        ("t_x", "Unrelated chat card", "builder", "/tmp/scratch", "default", 3, ""),
    ])
    conn.execute("INSERT INTO task_links VALUES ('t_b1','t_v1')")
    conn.executemany("INSERT INTO task_runs VALUES (?,?,?,?)", [(1, "builder", 1000, None), (2, "reviewer", 1700, None)])
    events = [
        ("t_b1", None, "created", {}, 900), ("t_v1", None, "created", {}, 901), ("t_x", None, "created", {}, 902),
        ("t_b1", 1, "claimed", {}, 1000), ("t_b1", 1, "heartbeat", {}, 1100),
        ("t_b1", 1, "review_requested", {"summary": "PR https://github.com/o/r/pull/7; 3 files"}, 1600),
        ("t_b1", 2, "changes_requested", {"reason": "R-1 | HIGH | a.js:4 | empty list crashes"}, 1650),
        ("t_b1", 2, "completed", {"summary": "APPROVED https://github.com/o/r/pull/7"}, 1800),
    ]
    for tid, run, kind, payload, ts in events:
        conn.execute("INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?,?,?,?,?)",
                     (tid, run, kind, json.dumps(payload), ts))
    return conn


def test_delivery_events_render_as_one_flow_once():
    conn, db = board(), FakeDB()
    state = flow.sync(conn, db, {}, now=900)  # backfill window covers everything
    sid = "cpipe_flow_t_b1"
    assert db.sessions == [(sid, "cpipe")] and db.titles[sid] == "⛓ Add export"
    texts = [m[2] for m in db.messages]
    assert all(m[0] == sid for m in db.messages)  # the chat card is not a delivery
    assert texts[0].startswith("## ⛓ Pipeline started") and "Build @builder → Verify @checker" in texts[0]
    assert "Repo: `app`" in texts[0]
    assert texts[1].startswith("▶ 🔨 **Build** started · @builder")
    assert "handed to review" in texts[2] and "10m 00s" in texts[2]
    assert texts[3].startswith("↩ **Review** requested changes") and "empty list crashes" in texts[3]
    assert texts[4].startswith("✅ 🔍 **Review** approved · @reviewer") and "PR: https://github.com/o/r/pull/7" in texts[4]
    assert "→ Next: **Verify** · @checker" in texts[4]
    assert len(texts) == 5  # second Verify `created` folded into the header, heartbeat skipped
    flow.sync(conn, db, state)
    assert len(db.messages) == 5  # cursor: nothing posted twice


def test_same_title_gets_worktree_suffix():
    db = FakeDB()
    db.titles["other"] = "⛓ Add export"
    flow.sync(board(), db, {}, now=900)
    assert db.titles["cpipe_flow_t_b1"] == "⛓ Add export · t_b1"
