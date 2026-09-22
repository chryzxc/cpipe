import runpy
import sqlite3
import subprocess
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
            completed_at REAL, workspace_path TEXT, last_failure_error TEXT,
            consecutive_failures INTEGER DEFAULT 0, result TEXT
        );
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, body TEXT, created_at REAL);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at REAL);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, profile TEXT, status TEXT,
            outcome TEXT, summary TEXT, error TEXT, ended_at REAL, claim_expires REAL);
        CREATE TABLE kanban_notify_subs (
            task_id TEXT NOT NULL, platform TEXT NOT NULL, chat_id TEXT NOT NULL,
            delivery_mode TEXT NOT NULL DEFAULT 'notify', created_at INTEGER NOT NULL,
            PRIMARY KEY (task_id, platform, chat_id)
        );
    """)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return conn


def add_card(conn, tid, status, title="Readiness card", assignee="sentry",
             failures=0, failure_error=None, result=None, workspace=None):
    conn.execute(
        "INSERT INTO tasks (id, title, status, assignee, skills, body, created_at,"
        " consecutive_failures, last_failure_error, result, workspace_path)"
        " VALUES (?, ?, ?, ?, '[]', 'BUDGET: token_budget 20m', ?, ?, ?, ?, ?)",
        (tid, title, status, assignee, time.time() - 7200, failures,
         failure_error, result, workspace))


def add_run(conn, tid, outcome, summary=None, ended_at=None):
    conn.execute(
        "INSERT INTO task_runs (task_id, profile, status, outcome, summary, ended_at)"
        " VALUES (?, ?, 'done', ?, ?, ?)",
        (tid, "sentry", outcome, summary, ended_at or (time.time() - 600)))


def add_comment(conn, tid, body):
    conn.execute(
        "INSERT INTO task_comments (task_id, body, created_at) VALUES (?, ?, ?)",
        (tid, body, time.time()))


def run_scan(monkeypatch, capsys):
    module = runpy.run_path(str(SCANNER_PATH))
    module["main"]()
    return capsys.readouterr().out


def git_workspace(tmp_path) -> str:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    env_opts = ("-c", "user.email=t@example.com", "-c", "user.name=t")
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), *env_opts,
                    "commit", "--allow-empty", "-q", "-m", "base"], check=True)
    head = subprocess.run(["git", "-C", str(workspace), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    return f"{workspace}@{head}"


def test_auth_failure_becomes_readiness_blocker_naming_profile(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "auth1", "ready",
             failure_error="provider rejected the request: 401 unauthorized (invalid api key)",
             failures=1)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "READINESS_BLOCKER · auth1 · profile 'sentry' cannot authenticate or start" in output
    assert "READINESS_BLOCKER" in output
    conn.close()


def test_empty_goal_mode_run_becomes_worker_empty_result(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    workspace = git_workspace(tmp_path)
    add_card(conn, "empty1", "blocked", workspace=workspace)
    add_run(conn, "empty1", "gave_up", summary=None)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "WORKER_EMPTY_RESULT · empty1 · gave_up run returned no usable response" in output
    assert workspace in output
    conn.close()


def test_respawn_loop_signals_at_engine_failure_limit(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    error = "worker exited cleanly (rc=0) without calling kanban_complete or kanban_block"
    add_card(conn, "loop1", "ready", failures=2, failure_error=error)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "RESPAWN_LOOP · loop1 · 2 consecutive failures without completion" in output
    conn.close()


def test_below_failure_limit_stays_silent(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    error = "worker exited cleanly (rc=0) without calling kanban_complete or kanban_block"
    add_card(conn, "single", "ready", failures=1, failure_error=error)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "RESPAWN_LOOP" not in output
    assert "READINESS_BLOCKER" not in output
    assert "WORKER_EMPTY_RESULT" not in output
    conn.close()


def test_marker_comments_dedupe_each_readiness_signal(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "auth2", "ready", failures=1,
             failure_error="401 unauthorized")
    add_card(conn, "loop2", "ready", failures=3, failure_error="pid 7 exited with code 1")
    add_card(conn, "empty2", "ready")
    add_run(conn, "empty2", "timed_out", summary=None)
    add_comment(conn, "auth2", "readiness_blocker: coordinator authenticating profile")
    add_comment(conn, "loop2", "respawn_bounded: recovery decision recorded")
    add_comment(conn, "empty2", "worker_empty_result: bounded correction queued")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "READINESS_BLOCKER · auth2" not in output
    assert "RESPAWN_LOOP · loop2" not in output
    assert "WORKER_EMPTY_RESULT · empty2" not in output
    conn.close()


def test_healthy_cards_stay_silent(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "fine", "review", result="summary recorded")
    add_run(conn, "fine", "completed", summary="implemented the slice")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "READINESS_BLOCKER" not in output
    assert "RESPAWN_LOOP" not in output
    assert "WORKER_EMPTY_RESULT" not in output
    conn.close()


def test_idle_board_classified_as_worker_readiness(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "auth3", "blocked", failures=1, failure_error="authentication failed")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "IDLE_BOARD · worker readiness (1 cards)" in output
    conn.close()


def test_auth_failure_after_completion_stays_silent(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "done1", "done", failures=0,
             failure_error="401 unauthorized from a previous attempt")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "READINESS_BLOCKER · done1" not in output
    conn.close()
