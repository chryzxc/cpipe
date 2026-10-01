"""``on_session_end`` in the worker: hand a finished build to review when the implementer forgot to.

A worker that pushes its work and then exits without ``kanban_request_review`` is booked as a crash
("rc=0 protocol violation") and respawned from scratch just to file the paperwork: a full extra run.
Before the worker process exits, if this run pushed a commit on a same-card-review card, file the
review request on its behalf. The run-id check inside the tool means it can only land on this run.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess

from . import review_gate, submit


def handoff(completed: bool = False, failed: bool = False, interrupted: bool = False, **_kw) -> None:
    tid, run_id = os.environ.get("HERMES_KANBAN_TASK"), os.environ.get("HERMES_KANBAN_RUN_ID")
    if not tid or not run_id or failed or interrupted:
        return
    try:
        summary = pushed_work(tid, int(run_id))
        if not summary:
            return
        from tools.kanban_tools import _handle_request_review
        out = _handle_request_review({"task_id": tid, "reviewer": submit._role("reviewer"), "summary": summary})
        submit._journal("handoff.auto_review", tid, ok='"error"' not in out, result=out[:300])
    except Exception:
        pass  # never break a worker exit on a plugin bug; Hermes' own retry still applies


def pushed_work(tid: str, run_id: int) -> str | None:
    """Handoff summary when this run is still open on a reviewed build card and pushed a commit, else None."""
    conn = sqlite3.connect(f"file:{submit._db()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT t.status, t.assignee, t.body, t.workspace_path, r.started_at FROM tasks t "
            "JOIN task_runs r ON r.id = t.current_run_id WHERE t.id = ? AND t.current_run_id = ?",
            (tid, run_id)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    status, assignee, body, ws, started = row
    if status != "running" or assignee != submit._role("implementer") or review_gate.MARKER not in (body or ""):
        return None

    def git(*args: str) -> str:
        r = subprocess.run(["git", "-C", ws, *args], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""

    head = git("rev-parse", "HEAD")
    if (not head or git("rev-parse", "@{u}") != head or git("status", "--porcelain", "--untracked-files=no")
            or int(git("log", "-1", "--format=%ct", "HEAD") or 0) < (started or 0)):
        return None  # nothing new pushed this run, or unpushed/uncommitted edits: let Hermes retry
    pr = subprocess.run(["gh", "pr", "view", "--json", "url", "-q", ".url"], cwd=ws,
                        capture_output=True, text=True, timeout=30).stdout.strip()
    files = git("show", "--name-only", "--format=", "HEAD").splitlines()
    return (f"Auto-handoff by the delivery plugin: the implementer pushed and exited without requesting review. "
            f"PR: {pr or 'none found'}; branch {git('rev-parse', '--abbrev-ref', 'HEAD')}; commit {head[:12]} "
            f"({git('log', '-1', '--format=%s', 'HEAD')}); last-commit files: {', '.join(files[:15]) or 'n/a'}. "
            "No test receipts were filed: rerun the checks yourself.")
