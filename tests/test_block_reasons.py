"""A blocked card explains itself once per block, including the bot's own errors from that run."""
import json
import sqlite3
import time
from datetime import datetime

from cpipe import block_reasons


def test_explains_each_block_once_with_run_errors(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE tasks (id TEXT, status TEXT, block_kind TEXT);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, profile TEXT, started_at REAL,
            ended_at REAL, outcome TEXT, error TEXT);
    """)
    now = time.time()
    conn.execute("INSERT INTO tasks VALUES ('t_a','blocked','transient'), ('t_ok','running',NULL)")
    conn.execute("INSERT INTO task_events (task_id, kind, payload) VALUES ('t_a','gave_up',?)",
                 (json.dumps({"error": "pid 1 not alive"}),))
    conn.execute("INSERT INTO task_runs (task_id, profile, started_at, ended_at, outcome, error) "
                 "VALUES ('t_a','archon',?,?,'crashed','pid 1 not alive')", (now - 60, now))
    logs = tmp_path / "profiles" / "archon" / "logs"
    logs.mkdir(parents=True)
    stamp = datetime.fromtimestamp(now - 30).strftime("%Y-%m-%d %H:%M:%S")
    (logs / "errors.log").write_text(
        f"{stamp},000 WARNING cli: Primary provider auth failed (Unknown provider 'x')\n"
        f"{stamp},000 WARNING tools.mcp_tool: MCP server 'y' failed\n"
        "2020-01-01 00:00:00,000 WARNING cli: Primary provider auth failed (old)\n")

    todo = block_reasons.pending(conn, {})
    assert [c["id"] for c in todo] == ["t_a"]
    text = block_reasons.explain(conn, todo[0], tmp_path)
    assert "pid 1 not alive" in text and "Unknown provider 'x'" in text
    assert "MCP server" not in text and "(old)" not in text
    assert block_reasons.pending(conn, {"t_a": todo[0]["event_id"]}) == []  # explained once
