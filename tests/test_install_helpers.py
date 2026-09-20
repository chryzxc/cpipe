import os
import runpy
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOLVE_PATH = ROOT / "workflow" / "scripts" / "resolve_role.py"
ROSTER_PATH = ROOT / "workflow" / "scripts" / "setup_roster.py"
SCANNER_PATH = ROOT / "workflow" / "scripts" / "kanban-supervisor-scan.py"


def run_resolver(roster_text: str, role: str, tmp_path: Path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir(exist_ok=True)
    (hermes_home / "roster.yaml").write_text(roster_text)
    env = dict(os.environ, HERMES_HOME=str(hermes_home))
    return subprocess.run(
        [sys.executable, str(RESOLVE_PATH), role],
        capture_output=True, text=True, env=env)


ROSTER = """# Role -> profile mapping
roles:
  coordinator: default   # main agent
  implementer: my-forge
"""


def test_resolve_role_parses_comments_and_values(tmp_path):
    result = run_resolver(ROSTER, "coordinator", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "default"


def test_resolve_role_hyphenated_profile(tmp_path):
    result = run_resolver(ROSTER, "implementer", tmp_path)
    assert result.stdout.strip() == "my-forge"


def test_resolve_role_unmapped_role_fails(tmp_path):
    result = run_resolver(ROSTER, "auditor", tmp_path)
    assert result.returncode == 1
    assert "unmapped" in result.stderr


def test_resolve_role_missing_roster_fails(tmp_path):
    hermes_home = tmp_path / "empty"
    hermes_home.mkdir()
    env = dict(os.environ, HERMES_HOME=str(hermes_home))
    result = subprocess.run(
        [sys.executable, str(RESOLVE_PATH), "coordinator"],
        capture_output=True, text=True, env=env)
    assert result.returncode == 1
    assert "missing roster" in result.stderr


def test_setup_roster_aliasing(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes"
    profiles = hermes_home / "profiles"
    for profile in ("default", "forge"):
        (profiles / profile).mkdir(parents=True)
    repo_roster = tmp_path / "repo-roster.yaml"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module = runpy.run_path(str(ROSTER_PATH))
    module["write_roster"].__globals__["REPO_ROSTER"] = repo_roster

    argv = ["setup_roster.py", "--roster", "coordinator=default", "implementer=forge",
            "reviewer=forge", "verifier=forge", "security_reviewer=forge"]
    monkeypatch.setattr(module["sys"], "argv", argv)
    assert module["main"]() == 0

    body = (hermes_home / "roster.yaml").read_text()
    assert "coordinator: default" in body
    for role in ("planner", "researcher", "spike_explorer", "designer", "release_engineer"):
        assert f"{role}: forge" in body
    assert "security_tester: forge" in body
    assert "auditor: default" in body  # aliases to coordinator
    assert repo_roster.read_text() == body


def test_setup_roster_rejects_unknown_profile(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles" / "default").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module = runpy.run_path(str(ROSTER_PATH))

    argv = ["setup_roster.py", "--roster", "coordinator=default", "implementer=ghost",
            "reviewer=ghost", "verifier=ghost", "security_reviewer=ghost"]
    monkeypatch.setattr(module["sys"], "argv", argv)
    assert module["main"]() == 1


def test_scanner_caps_workspace_checks(tmp_path, monkeypatch, capsys):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    conn = __import__("sqlite3").connect(hermes_home / "kanban.db")
    conn.executescript(
        "CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, assignee TEXT,"
        " skills TEXT, body TEXT, created_at REAL, started_at REAL, last_heartbeat_at REAL,"
        " completed_at REAL, workspace_path TEXT, last_failure_error TEXT);"
        "CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, body TEXT, created_at REAL);"
        "CREATE TABLE task_events (task_id TEXT, kind TEXT, payload TEXT, created_at REAL);"
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, outcome TEXT, ended_at REAL, claim_expires REAL);")
    for i in range(40):
        conn.execute(
            "INSERT INTO tasks (id, title, status, assignee, skills, body, created_at,"
            " started_at, last_heartbeat_at, completed_at, workspace_path, last_failure_error)"
            " VALUES (?, 'ready card', 'ready', 'forge', '[]', 'BUDGET: token_budget 20m',"
            " ?, NULL, NULL, NULL, ?, NULL)",
            (f"r_{i}", time.time() - 60, f"/nonexistent/ws_{i}"))
    conn.commit()
    conn.close()

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module = runpy.run_path(str(SCANNER_PATH))
    module["main"]()

    output = capsys.readouterr().out
    assert output.count("WORKSPACE_INVALID") == 32
    assert "WORKSPACE_CAP · 8" in output
