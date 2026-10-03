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
            body TEXT, created_at REAL, started_at REAL, last_heartbeat_at REAL, completed_at REAL,
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
        "INSERT INTO tasks (id, title, status, assignee, skills, body, created_at)"
        " VALUES (?, ?, ?, ?, '[]', 'BUDGET: token_budget 20m', ?)",
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


def test_supervisor_prompt_carries_push_rules():
    defs = json.loads((ROOT / "workflow" / "cron.jobs.json").read_text())
    prompt = next(j for j in defs if j["name"] == "Kanban stall supervisor")["prompt"]
    for rule in ("UNSUBSCRIBED_CARD:", "notify+wake", "ORPHANED_CHAIN:", "UNSUBSCRIBED_BLOCK:", "SUPERSEDED_REVIEW",
                 "at most ONE coordinator invocation per tick", "coordinator_wake", "orphan_wake",
                 "INFORMATIONAL-ONLY TICKS", "wake NO ONE"):
        assert rule in prompt
    assert "never run `hermes kanban dispatch`" in prompt and "--source tool" not in prompt


def test_supervisor_prompt_is_small_and_each_rule_appears_once():
    import re
    defs = json.loads((ROOT / "workflow" / "cron.jobs.json").read_text())
    prompt = next(j for j in defs if j["name"] == "Kanban stall supervisor")["prompt"]
    assert len(prompt) < 3000
    rules = re.findall(r"^([A-Z][A-Z_]+):", prompt, re.M)
    assert rules and len(rules) == len(set(rules))
    scan = (ROOT / "workflow" / "scripts" / "kanban-supervisor-scan.py").read_text()
    for signal in set(re.findall(r'f?"([A-Z][A-Z_]{4,}) ·', scan)):
        assert signal in prompt, f"scan emits {signal} but the prompt has no rule for it"


def test_policy_files_carry_push_contract():
    skill = (ROOT / "workflow" / "skills" / "my-cpipe-orchestrator" / "SKILL.md").read_text()
    assert "notify-list" in skill
    assert "never through the operator asking" in skill
    assert "CONTINUATION:" in skill
    assert "record supersession as a comment linking the replacement card-id" in skill
    assert "ask with the `clarify` tool" in skill and "never pick an option yourself" in skill
