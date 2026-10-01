"""An implementer that pushed this run but exited without request-review gets the review filed for it."""
import sqlite3
import subprocess
import time

from software_delivery import exit_handoff, review_gate, submit


def board(tmp_path, monkeypatch, ws, body=review_gate.MARKER, status="running", started=None):
    (tmp_path / "roster.yaml").write_text("roles:\n  implementer: forge\n  reviewer: sentry\n")
    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path)
    db = tmp_path / "kanban.db"
    db.unlink(missing_ok=True)
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks (id, status, assignee, body, workspace_path, current_run_id)")
    conn.execute("CREATE TABLE task_runs (id, started_at)")
    conn.execute("INSERT INTO tasks VALUES ('t_1', ?, 'forge', ?, ?, 7)", (status, body, str(ws)))
    conn.execute("INSERT INTO task_runs VALUES (7, ?)", (started if started is not None else time.time() - 60,))
    conn.commit()
    conn.close()


def repo(tmp_path, push=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    remote, ws = tmp_path / "remote.git", tmp_path / "ws"
    sh = lambda *a, cwd=tmp_path: subprocess.run(a, cwd=cwd, check=True, capture_output=True)
    sh("git", "init", "--bare", "-q", str(remote))
    sh("git", "clone", "-q", str(remote), str(ws))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        sh("git", "config", k, v, cwd=ws)
    (ws / "a.txt").write_text("x")
    sh("git", "add", ".", cwd=ws)
    sh("git", "commit", "-qm", "fix: a", cwd=ws)
    if push:
        sh("git", "push", "-q", "-u", "origin", "HEAD", cwd=ws)
    return ws


def test_pushed_commit_this_run_yields_summary(tmp_path, monkeypatch):
    ws = repo(tmp_path)
    board(tmp_path, monkeypatch, ws)
    summary = exit_handoff.pushed_work("t_1", 7)
    assert summary and "a.txt" in summary and "fix: a" in summary


def test_no_handoff_when_unpushed_old_or_not_a_build_card(tmp_path, monkeypatch):
    ws = repo(tmp_path, push=False)
    board(tmp_path, monkeypatch, ws)
    assert exit_handoff.pushed_work("t_1", 7) is None  # committed but not pushed
    ws = repo(tmp_path / "b")
    board(tmp_path, monkeypatch, ws, started=time.time() + 3600)
    assert exit_handoff.pushed_work("t_1", 7) is None  # commit predates this run
    board(tmp_path, monkeypatch, ws, body="hand-made card")
    assert exit_handoff.pushed_work("t_1", 7) is None
    board(tmp_path, monkeypatch, ws, status="review")
    assert exit_handoff.pushed_work("t_1", 7) is None  # worker already handed off
    assert exit_handoff.pushed_work("t_1", 8) is None  # a newer run owns the card
