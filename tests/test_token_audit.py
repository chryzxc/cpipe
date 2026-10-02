import json
import runpy
import sqlite3
import time
from pathlib import Path

AUDIT_PATH = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "token_audit.py"


def test_audit_scales_long_sessions_to_the_window_and_diffs_against_the_last_run(tmp_path, monkeypatch):
    now = time.time()
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.executescript("""
        CREATE TABLE sessions (id TEXT, source TEXT, input_tokens INT, output_tokens INT, cache_read_tokens INT,
            cache_write_tokens INT, api_call_count INT, title TEXT, started_at REAL, last_activity_at REAL);
        CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, timestamp REAL);
    """)
    conn.execute("INSERT INTO sessions VALUES ('s1','herm',1000,0,0,0,4,'chat',?,?)", (now - 9 * 86400, now))
    skill = json.dumps([{"function": {"name": "skill_view", "arguments": json.dumps({"name": "hermes-agent"})}}])
    conn.execute("INSERT INTO messages VALUES ('s1','assistant','',NULL,?)", (now - 9 * 86400,))  # outside window
    conn.execute("INSERT INTO messages VALUES ('s1','assistant','',?,?)", (skill, now - 60))
    conn.commit()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    audit = runpy.run_path(str(AUDIT_PATH))

    snap = audit["collect"](24)
    assert snap["bots"]["nexus"]["tokens"] == 500  # half of the session's turns fall inside the window
    assert snap["coordinator"]["skill_loads"] == {"hermes-agent": 1}

    later = dict(snap, total_tokens=2000)
    assert "total_tokens: 500 → 2,000" in audit["render"](later, snap)
    half_day = dict(snap, hours=12)  # 500 in 12h is the same daily rate as 1000 in 24h
    assert "total_tokens: 1,000 → 2,000" in audit["render"](later, half_day)
