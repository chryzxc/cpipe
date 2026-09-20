import json
import runpy
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER_PATH = ROOT / "workflow" / "scripts" / "kanban-supervisor-scan.py"
STATE_FILE = "logs/supervisor-progress-state.json"


def scanner(tmp_path, monkeypatch, with_subs_table=True):
    hermes_home = tmp_path / "hermes"
    (hermes_home / "logs").mkdir(parents=True)
    conn = sqlite3.connect(hermes_home / "kanban.db")
    tables = """
        CREATE TABLE tasks (
            id TEXT, title TEXT, status TEXT, assignee TEXT, skills TEXT,
            body TEXT, created_at REAL, started_at REAL, last_heartbeat_at REAL,
            workspace_path TEXT, last_failure_error TEXT
        );
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, body TEXT, created_at REAL);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at REAL);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, outcome TEXT, ended_at REAL, claim_expires REAL);
    """
    if with_subs_table:
        tables += """
        CREATE TABLE kanban_notify_subs (
            task_id TEXT NOT NULL, platform TEXT NOT NULL, chat_id TEXT NOT NULL,
            delivery_mode TEXT NOT NULL DEFAULT 'notify', created_at INTEGER NOT NULL,
            PRIMARY KEY (task_id, platform, chat_id)
        );
        """
    conn.executescript(tables)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return conn, hermes_home


def add_card(conn, tid, status, assignee="forge", title="Active card"):
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, '[]', 'BUDGET: token_budget 20m',"
        " ?, NULL, NULL, NULL, NULL)",
        (tid, title, status, assignee, time.time()))


def subscribe(conn, tid, mode="notify+wake"):
    conn.execute(
        "INSERT INTO kanban_notify_subs (task_id, platform, chat_id, delivery_mode, created_at)"
        " VALUES (?, 'tui', 'sess1', ?, ?)",
        (tid, mode, time.time()))


def add_comment(conn, tid, body):
    cur = conn.execute(
        "INSERT INTO task_comments (task_id, body, created_at) VALUES (?, ?, ?)",
        (tid, body, time.time()))
    return cur.lastrowid


def run_scan(monkeypatch, capsys):
    module = runpy.run_path(str(SCANNER_PATH))
    module["main"]()
    return capsys.readouterr().out


def state(tmp_path):
    path = tmp_path / "hermes" / STATE_FILE
    return json.loads(path.read_text()) if path.exists() else None


def test_unsubscribed_active_card_signalled(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "lonely", "running")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "UNSUBSCRIBED_CARD · lonely · running card has no wake-capable notify subscription" in output
    conn.close()


def test_wake_subscription_silences_signal(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "wired", "running")
    subscribe(conn, "wired", "notify+wake")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "UNSUBSCRIBED_CARD" not in output
    conn.close()


def test_passive_notify_only_still_signals(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "passive", "ready")
    subscribe(conn, "passive", "notify")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "UNSUBSCRIBED_CARD · passive" in output
    conn.close()


def test_unassigned_todo_card_exempt(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "raw", "todo", assignee="")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "UNSUBSCRIBED_CARD · raw" not in output
    conn.close()


def test_unsubscribed_deduped_by_comment(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "lonely", "running")
    add_comment(conn, "lonely", "unsubscribed_card: coordinator waking to subscribe")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "UNSUBSCRIBED_CARD" not in output
    conn.close()


def test_missing_subs_table_suppresses_signal(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch, with_subs_table=False)
    add_card(conn, "lonely", "running")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "UNSUBSCRIBED_CARD" not in output
    conn.close()


def test_progress_delta_first_run_silent_but_records(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "one", "running")
    add_comment(conn, "one", "heartbeat PRECHECK")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "PROGRESS_DELTA" not in output
    recorded = state(tmp_path)
    assert recorded["one"]["status"] == "running"
    conn.close()


def test_progress_delta_reports_status_change(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "two", "ready")
    conn.commit()
    run_scan(monkeypatch, capsys)

    conn.execute("UPDATE tasks SET status='running' WHERE id='two'")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "PROGRESS_DELTA · two · ready→running" in output
    conn.close()


def test_progress_delta_reports_new_heartbeat_only(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "three", "running")
    add_comment(conn, "three", "heartbeat RED")
    conn.commit()
    run_scan(monkeypatch, capsys)

    add_comment(conn, "three", "heartbeat GREEN")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "PROGRESS_DELTA · three · heartbeat" in output
    conn.close()


def test_progress_delta_reports_completion(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "four", "review")
    conn.commit()
    run_scan(monkeypatch, capsys)

    conn.execute("UPDATE tasks SET status='done' WHERE id='four'")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "PROGRESS_DELTA · four · review→done" in output
    conn.close()


def test_progress_delta_silent_when_unchanged(tmp_path, monkeypatch, capsys):
    conn, _ = scanner(tmp_path, monkeypatch)
    add_card(conn, "five", "running")
    add_comment(conn, "five", "heartbeat PRECHECK")
    conn.commit()
    run_scan(monkeypatch, capsys)
    first_state = state(tmp_path)

    output = run_scan(monkeypatch, capsys)

    assert "PROGRESS_DELTA" not in output
    assert state(tmp_path) == first_state
    conn.close()


def test_supervisor_prompt_carries_push_rules():
    defs = json.loads((ROOT / "workflow" / "cron.jobs.json").read_text())
    supervisor = next(j for j in defs if j["name"] == "Kanban stall supervisor")
    assert "UNSUBSCRIBED_CARD:" in supervisor["prompt"]
    assert "notify+wake" in supervisor["prompt"]
    assert "PROGRESS_DELTA" in supervisor["prompt"]


def test_policy_files_carry_push_contract():
    skill = (ROOT / "workflow" / "skills" / "my-software-delivery-orchestrator" / "SKILL.md").read_text()
    assert "notify-list" in skill
    assert "never through the operator asking" in skill
