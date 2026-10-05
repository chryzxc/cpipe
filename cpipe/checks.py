"""Verify runs beside review, and cpipe joins the two. Deterministic, no LLM.

Review then verify, each waiting on the last, made checks take their sum, and a verify failure found
after approval cost a fix card, a second review and a second verify. On each dispatch tick:

* release: a build or fix card handed to review (status `review`) whose `Verify:` child still waits
  gets that verify started now, in its own detached checkout of the handed-off commit (the verify's
  scratch dir), so PROOF's BASE checkout never touches the worktree the reviewer reads. Hermes
  starts a child only when every parent is done, so the link is dropped; the verify body's
  `BUILD CARD:` line keeps the relation.
* join: once the build card is approved (done) and its verify is done, PASS on the approved commit
  marks the PR ready. PASS on an older commit (review asked for changes after it), or a FAIL that
  went back into the open review round, gets a delta verify of what changed since. A FAIL after
  approval already opened a fix card (``delivery_verify_failed``).
"""

from __future__ import annotations

import fcntl
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import review_gate, submit
from .home import root as _root_home

VERDICT = re.compile(r"VERDICT:\s*(PASS|FAIL)\b(?:\s+([0-9a-f]{7,40}))?", re.I)
BUILD_LINE = re.compile(r"^BUILD CARD: (t_[0-9a-f]+)", re.M)

VERIFY_NOTE = ("PARALLEL CHECK: {build} was handed to review at {sha}. Your workspace is a detached checkout of "
               "that commit, so you verify while the reviewer reviews; do not cd into {worktree}. The implementer's "
               "handoff (PR url, PINNED tests, commands run):\n{summary}")
BUILD_NOTE = ("PARALLEL CHECK: verify {verify} runs on {sha} alongside this review. Reviewer: on approval leave the PR "
              "draft; cpipe marks it ready once verify passes on the approved commit.")
DELTA_NOTE = ("DELTA: an earlier verify of this change ({verify}, {verdict} at {sha}) ran before the last review "
              "round. Run PROOF and RELATED only for files changed since (`git diff --name-only {sha}..HEAD`), and "
              "re-run every command its result reports failing.")


class Board:
    """In-process kanban writes: a `hermes` CLI call from the gateway tick can rebuild the plugin for minutes."""

    def __init__(self, conn):
        from hermes_cli import kanban_db
        self.conn, self.kb = conn, kanban_db

    def comment(self, tid: str, text: str) -> None:
        self.kb.add_comment(self.conn, tid, "cpipe", text)

    def unlink(self, parent: str, child: str) -> None:
        self.kb.unlink_tasks(self.conn, parent, child)

    def assign(self, tid: str, who: str) -> None:
        self.kb.assign_task(self.conn, tid, who)


def _git(cwd: Optional[str], *args: str, timeout: int = 120) -> Optional[str]:
    if not cwd:
        return None
    try:
        out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def build_of(conn, verify_id: str, body: Optional[str] = None):
    """(id, status, workspace_path, title, body) of the card a verify checks: its parent while linked, else
    its `BUILD CARD:` line."""
    row = conn.execute("SELECT t.id FROM task_links l JOIN tasks t ON t.id = l.parent_id WHERE l.child_id = ? "
                       f"AND {submit.step_sql('t')} != 'verify' LIMIT 1", (verify_id,)).fetchone()
    if not row:
        if body is None:
            found = conn.execute("SELECT body FROM tasks WHERE id = ?", (verify_id,)).fetchone()
            body = found[0] if found else ""
        m = BUILD_LINE.search(body or "")
        row = (m[1],) if m else None
    if not row:
        return None
    return conn.execute("SELECT id, status, workspace_path, title, body FROM tasks WHERE id = ?", (row[0],)).fetchone()


def release(conn, board, root: Path, state: dict) -> list[str]:
    """Start every verify whose build card is in review. Returns the released verify ids."""
    rows = conn.execute(
        "SELECT v.id, b.id, b.workspace_path, b.project_id FROM tasks v JOIN task_links l ON l.child_id = v.id "
        f"JOIN tasks b ON b.id = l.parent_id WHERE {submit.step_sql('v')} = 'verify' AND v.status = 'todo' "
        "AND b.status = 'review' AND instr(b.body, ?) > 0 "
        "AND (SELECT count(*) FROM task_links x WHERE x.child_id = v.id) = 1",
        (review_gate.MARKER,)).fetchall()
    released = []
    for verify, build, worktree, project in rows:
        sha = _git(worktree, "rev-parse", "HEAD")
        checkout = root / verify
        if not sha or (checkout.exists() and any(checkout.iterdir())):
            continue
        # ponytail: checkout runs in the tick (seconds on a big repo); move to a thread if ticks lag.
        if _git(worktree, "worktree", "add", "--detach", str(checkout), sha) is None:
            continue
        review_gate.route_verify(build, worktree, project, board=board)  # reads the link: before unlink
        handoff = conn.execute("SELECT payload FROM task_events WHERE task_id = ? AND kind = 'review_requested' "
                               "ORDER BY id DESC LIMIT 1", (build,)).fetchone()
        summary = (json.loads(handoff[0] or "{}").get("summary") if handoff else "") or "see kanban_show " + build
        board.comment(verify, VERIFY_NOTE.format(build=build, sha=sha, worktree=worktree, summary=summary))
        board.comment(build, BUILD_NOTE.format(verify=verify, sha=sha[:12]))
        board.unlink(build, verify)
        state.setdefault("released", {})[verify] = sha
        submit._journal("verify.parallel", verify, build=build, sha=sha)
        released.append(verify)
    return released


def _verdict(conn, verify: str, result: Optional[str]):
    text = result or ""
    if not VERDICT.search(text):
        row = conn.execute("SELECT summary FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
                           (verify,)).fetchone()
        text = (row and row[0]) or ""
    m = VERDICT.search(text)
    return (m[1].upper(), m[2]) if m else (None, None)


def _delta(build_row, verify: str, verify_body: str, verdict: str, sha: str) -> None:
    build, _status, _wt, title, body = build_row
    project = submit._task_row(build)
    argv, _ = submit.chained_card("verify", title, submit._request_of(body), (project and project["project_id"]) or "",
                                  build, "small")
    extra = DELTA_NOTE.format(verify=verify, verdict=verdict, sha=sha[:12])
    live_qa = submit.LIVE_QA_RE.search(verify_body or "")
    argv = submit._add_body(argv, extra + ("\n" + live_qa.group(0) if live_qa else ""))
    # ponytail: the CLI create can stall on a plugin rebuild; a thread keeps the tick moving.
    threading.Thread(target=lambda: (tid := submit._create(argv)[0]) and submit._tag(tid, "delta", "verify"),
                     daemon=True).start()
    submit._journal("verify.delta", build, earlier=verify, sha=sha)


def join(conn, root: Path, state: dict, *, ready=None, delta=None) -> list[tuple[str, str]]:
    """Act on every verify done since cpipe started joining. Returns (verify id, action) pairs."""
    ready = ready or (lambda wt: subprocess.run(["gh", "pr", "ready"], cwd=wt, capture_output=True, timeout=60))
    delta = delta or _delta
    joined = state.setdefault("joined", [])
    rows = conn.execute(
        f"SELECT id, body, result FROM tasks WHERE {submit.step_sql()} = 'verify' AND status = 'done' "
        "AND created_by = 'delivery_submit' AND completed_at >= ?", (int(state["since"]),)).fetchall()
    done = []
    for verify, body, result in rows:
        if verify in joined:
            continue
        build = build_of(conn, verify, body)
        if build and build[1] not in ("done", "archived"):
            continue  # review still open: wait for its verdict
        joined.append(verify)
        action = "none"
        if build and build[1] == "done" and (head := _git(build[2], "rev-parse", "HEAD")):
            verdict, sha = _verdict(conn, verify, result)
            sha = sha or state.get("released", {}).get(verify) or head
            same = head.startswith(sha) or sha.startswith(head)
            fixed = conn.execute("SELECT 1 FROM task_links l JOIN tasks t ON t.id = l.child_id WHERE l.parent_id = ? "
                                 f"AND {submit.step_sql('t')} = 'fix'", (verify,)).fetchone()
            if verdict == "PASS" and same:
                ready(build[2])
                action = "ready"
            elif verdict == "PASS" or (verdict == "FAIL" and not fixed):
                delta(build, verify, body, verdict, sha)
                action = "delta"
            elif verdict is None:
                action = "no_verdict"
            if verify in state.get("released", {}):
                _git(build[2], "worktree", "remove", "--force", str(root / verify))
                _git(build[2], "worktree", "prune")
        submit._journal(f"verify.join.{action}", verify, build=build and build[0])
        done.append((verify, action))
    del joined[:-500]
    return done


def run_checks(*, dry_run: bool = False, board: Optional[str] = None, home: Optional[Path] = None,
               **_: Any) -> None:
    """``on_kanban_dispatch_tick`` observer (gateway process). Never raises."""
    if dry_run or board not in (None, "", "default"):
        return
    try:
        home = home or _root_home()
        state_path = home / "state" / "cpipe-checks.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path.with_suffix(".lock"), "w") as lock:
            try:  # a CLI `hermes kanban dispatch` ticks too: one joiner at a time
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return
            try:
                state = json.loads(state_path.read_text())
            except (OSError, ValueError):
                state = {}
            state.setdefault("since", time.time())  # never act on verifies finished before this existed
            from hermes_cli import kanban_db
            from hermes_cli.kanban_db_connect import connect_closing
            root = kanban_db.workspaces_root()
            with connect_closing() as conn:
                release(conn, Board(conn), root, state)
                join(conn, root, state)
            state_path.write_text(json.dumps(state))
    except Exception:
        pass
