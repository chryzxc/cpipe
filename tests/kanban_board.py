"""The real kanban.db schema (Hermes kanban_db) plus builders, so monitor tests replay real board shapes."""
import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE tasks (
    id                   TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    body                 TEXT,
    assignee             TEXT,
    status               TEXT NOT NULL,
    priority             INTEGER DEFAULT 0,
    created_by           TEXT,
    created_at           INTEGER NOT NULL,
    started_at           INTEGER,
    completed_at         INTEGER,
    workspace_kind       TEXT NOT NULL DEFAULT 'scratch',
    workspace_path       TEXT,
    claim_lock           TEXT,
    claim_expires        INTEGER,
    tenant               TEXT,
    result               TEXT,
    idempotency_key      TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    worker_pid           INTEGER,
    last_failure_error   TEXT,
    max_runtime_seconds  INTEGER,
    last_heartbeat_at    INTEGER,
    current_run_id       INTEGER,
    workflow_template_id TEXT,
    current_step_key     TEXT,
    skills               TEXT,
    max_retries          INTEGER
, branch_name TEXT, project_id TEXT, model_override TEXT, provider_override TEXT, goal_mode INTEGER NOT NULL DEFAULT 0, goal_max_turns INTEGER, session_id TEXT, block_kind TEXT, block_recurrences INTEGER NOT NULL DEFAULT 0, reasoning_effort TEXT, completion_contract TEXT, worker_started_at INTEGER);
CREATE TABLE task_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id             TEXT NOT NULL,
    profile             TEXT,
    step_key            TEXT,
    status              TEXT NOT NULL,
    claim_lock          TEXT,
    claim_expires       INTEGER,
    worker_pid          INTEGER,
    max_runtime_seconds INTEGER,
    last_heartbeat_at   INTEGER,
    started_at          INTEGER NOT NULL,
    ended_at            INTEGER,
    outcome             TEXT,
    summary             TEXT,
    metadata            TEXT,
    error               TEXT
, worker_started_at INTEGER);
CREATE TABLE task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    run_id     INTEGER,
    kind       TEXT NOT NULL,
    payload    TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE task_links (
    parent_id  TEXT NOT NULL,
    child_id   TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);
CREATE TABLE kanban_notify_subs (
    task_id       TEXT NOT NULL,
    platform      TEXT NOT NULL,
    chat_id       TEXT NOT NULL,
    thread_id     TEXT NOT NULL DEFAULT '',
    user_id       TEXT,
    created_at    INTEGER NOT NULL,
    last_event_id INTEGER NOT NULL DEFAULT 0, notifier_profile TEXT, chat_type TEXT, delivery_metadata TEXT, delivery_mode TEXT NOT NULL DEFAULT 'notify', user_id_alt TEXT, last_ping_event_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (task_id, platform, chat_id, thread_id)
);
CREATE TABLE task_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    author     TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""


def board(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def ago(minutes):
    return int(time.time() - minutes * 60)


def card(conn, tid, status, *, minutes_ago=0, assignee="forge", title=None, **cols):
    cols = {"id": tid, "title": title or f"Card {tid}", "assignee": assignee, "status": status,
            "created_at": ago(minutes_ago), **cols}
    conn.execute(f"INSERT INTO tasks ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    event(conn, tid, {"blocked": "blocked", "triage": "block_loop_detected", "running": "claimed",
                      "review": "review_requested"}.get(status, "created"), minutes_ago)
    conn.commit()
    return tid


def event(conn, tid, kind, minutes_ago=0, **payload):
    conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                 (tid, kind, json.dumps(payload), ago(minutes_ago)))
    conn.commit()


def run(conn, tid, outcome, *, started_ago, seconds=600, error=None, profile="forge"):
    start = ago(started_ago)
    conn.execute("INSERT INTO task_runs (task_id, profile, status, started_at, ended_at, outcome, error) "
                 "VALUES (?,?,?,?,?,?,?)", (tid, profile, "done", start, start + seconds, outcome, error))
    conn.commit()


def link(conn, parent, child):
    conn.execute("INSERT INTO task_links VALUES (?,?)", (parent, child))
    conn.commit()


def subscribe(conn, tid, chat="chat-1"):
    conn.execute("INSERT INTO kanban_notify_subs (task_id, platform, chat_id, created_at, delivery_mode) "
                 "VALUES (?,?,?,?,?)", (tid, "discord", chat, ago(0), "notify+wake"))
    conn.commit()
