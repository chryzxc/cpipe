"""Verify runs beside review, and cpipe joins the two. Deterministic, no LLM.

Review then verify, each waiting on the last, made checks take their sum, and a verify failure found
after approval cost a fix card, a second review and a second verify. On each dispatch tick:

* release: a build or fix card handed to review (status `review`) whose `Verify:` child still waits
  gets that verify started now, in its own detached checkout of the handed-off commit (the verify's
  scratch dir), so PROOF's BASE checkout never touches the worktree the reviewer reads. Hermes
  starts a child only when every parent is done, so the link is dropped; the verify body's
  `BUILD CARD:` line keeps the relation.
* join: a finished verify's outcome (``outcome``) picks the next step from ``submit.EDGES``: PASS on
  the approved commit marks the PR ready, a FAIL while review is open joins that round, PASS or FAIL
  on an older commit gets a delta verify of what changed since, a FAIL on the approved commit opens
  the fix flow.
"""

from __future__ import annotations

import fcntl
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from . import review_gate, submit
from .home import root as _root_home

VERDICT = re.compile(r"VERDICT:\s*(PASS|FAIL)\b(?:\s+([0-9a-f]{7,40}))?", re.I)
BUILD_LINE = re.compile(r"^BUILD CARD: (t_[0-9a-f]+)", re.M)
# The dispatcher's respawn guard holds any card with a PR url in a recent comment ("active_pr"), so a
# verify handed that url would never start. owner/repo#N names the same PR (`gh pr view N -R owner/repo`).
PR_URL = re.compile(r"https?://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)", re.I)
PR_GUARD_WINDOW = 86400  # the guard's own window

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
        board.comment(verify, VERIFY_NOTE.format(build=build, sha=sha, worktree=worktree,
                                                 summary=PR_URL.sub(r"\1/\2#\3", summary)))
        board.comment(build, BUILD_NOTE.format(verify=verify, sha=sha[:12]))
        board.unlink(build, verify)
        state.setdefault("released", {})[verify] = sha
        submit._journal("verify.parallel", verify, build=build, sha=sha)
        released.append(verify)
    return released


def lift_pr_guards(conn, board, now: Optional[float] = None) -> list[str]:
    """Start ready cards the active_pr guard holds over a PR url someone else posted (an implementer's
    handoff on its verify, a coordinator note). The guard exists so the worker that opened a PR is not
    re-spawned into a duplicate; it lifts on a handoff, which is what an unassign/reassign records."""
    now = now or time.time()
    lifted = []
    for tid, who in conn.execute(
            "SELECT t.id, t.assignee FROM tasks t WHERE t.status = 'ready' AND t.assignee IS NOT NULL AND EXISTS "
            "(SELECT 1 FROM task_events e WHERE e.task_id = t.id AND e.kind = 'respawn_guarded' "
            "AND e.payload LIKE '%active_pr%' AND e.created_at > ?)", (now - 300,)).fetchall():
        url_by = next((a for a, body in conn.execute(
            "SELECT author, body FROM task_comments WHERE task_id = ? AND created_at >= ? ORDER BY id DESC",
            (tid, now - PR_GUARD_WINDOW)) if PR_URL.search(body or "")), None)
        if not url_by or url_by == who:
            continue  # its own worker opened that PR: the guard is right
        board.assign(tid, None)
        board.assign(tid, who)
        submit._journal("pr_guard.lifted", tid, url_by=url_by, assignee=who)
        lifted.append(tid)
    return lifted


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
    argv = submit._add_body(argv, f"DELIVERY: {submit.delivery_of(build, body)}")
    threading.Thread(target=lambda: (tid := submit._create(argv)[0]) and submit._tag(tid, "delta", "verify"),
                     daemon=True).start()
    submit._journal("verify.delta", build, earlier=verify, sha=sha)


def outcome(conn, verify: str, body: Optional[str], result: Optional[str], state: dict):
    """(submit.EDGES outcome, the card it checks, verdict, sha) of a finished verify."""
    build = build_of(conn, verify, body)
    verdict, sha = _verdict(conn, verify, result)
    if build and build[1] not in ("done", "archived"):
        return ("fail_review" if verdict == "FAIL" else "wait"), build, verdict, sha
    head = build and build[1] == "done" and _git(build[2], "rev-parse", "HEAD")
    if not head:
        return "orphan", build, verdict, sha
    sha = sha or state.get("released", {}).get(verify) or head
    same = head.startswith(sha) or sha.startswith(head)
    if verdict is None:
        return "no_verdict", build, verdict, sha
    if verdict == "PASS":
        return ("pass" if same else "pass_old"), build, verdict, sha
    fixing = conn.execute("SELECT 1 FROM task_links l JOIN tasks t ON t.id = l.child_id WHERE l.parent_id = ? "
                          f"AND {submit.step_sql('t')} = 'fix'", (verify,)).fetchone()
    return ("fixing" if fixing else "fail" if same else "fail_old"), build, verdict, sha


def _fix(verify: str, result: str) -> None:
    """The fix flow for a verify that failed on the approved commit (what the verifier's tool call did)."""
    def run():
        out = json.loads(submit.verify_failed({"task_id": verify, "failures": result}))
        submit._journal("verify.fix" if out.get("ok") else "verify.fix_failed", verify, error=out.get("error"))
    threading.Thread(target=run, daemon=True).start()  # CLI creates: see _delta


def _to_review(conn, board, build: str, verify: str, result: str) -> None:
    """Failures found while review is open go to that round, once (a verifier's tool call may have sent them)."""
    head = f"VERIFY FAILED ({verify}"
    if conn.execute("SELECT 1 FROM task_comments WHERE task_id = ? AND instr(body, ?) > 0", (build, head)).fetchone():
        return
    board.comment(build, f"{head}, run beside this review):\n{result}\nReviewer: a High finding unless its failing "
                         "command passes at HEAD. Implementer: fix it with the review findings.")


# what the tick does for each submit.EDGES action of a finished verify
ACTIONS = {
    "ready": lambda c, v, b, body, verdict, sha, result: c.ready(b[2]),
    "delta": lambda c, v, b, body, verdict, sha, result: c.delta(b, v, body, verdict, sha),
    "fix": lambda c, v, b, body, verdict, sha, result: c.fix(v, result),
    "to_review": lambda c, v, b, body, verdict, sha, result: c.board and _to_review(c.conn, c.board, b[0], v, result),
    "none": lambda *a: None,
}


def join(conn, root: Path, state: dict, *, ready=None, delta=None, fix=None, board=None) -> list[tuple[str, str]]:
    """Follow submit.EDGES for every verify done since cpipe started joining. Returns (verify id, action) pairs."""
    ready = ready or (lambda wt: subprocess.run(["gh", "pr", "ready"], cwd=wt, capture_output=True, timeout=60))
    ctx = SimpleNamespace(conn=conn, board=board, ready=ready, delta=delta or _delta, fix=fix or _fix)
    joined = state.setdefault("joined", [])
    routed = state.setdefault("routed", [])
    rows = conn.execute(
        f"SELECT id, body, coalesce(result, '') FROM tasks WHERE {submit.step_sql()} = 'verify' AND status = 'done' "
        "AND created_by = 'delivery_submit' AND completed_at >= ?", (int(state["since"]),)).fetchall()
    done = []
    for verify, body, result in rows:
        if verify in joined:
            continue
        name, build, verdict, sha = outcome(conn, verify, body, result, state)
        result = result.strip() or f"see the result of {verify} (kanban_show {verify})"
        action = submit.EDGES[("verify", name)]
        if name in ("wait", "fail_review"):  # review still open: join once it is approved
            if action != "none" and verify not in routed:
                routed.append(verify)
                ACTIONS[action](ctx, verify, build, body, verdict, sha, result)
                done.append((verify, action))
            continue
        joined.append(verify)
        ACTIONS[action](ctx, verify, build, body, verdict, sha, result)
        if build and verify in state.get("released", {}):
            _git(build[2], "worktree", "remove", "--force", str(root / verify))
            _git(build[2], "worktree", "prune")
        submit._journal(f"verify.join.{name}", verify, build=build and build[0], action=action)
        done.append((verify, action))
    del joined[:-500], routed[:-500]
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
                lift_pr_guards(conn, Board(conn))
                release(conn, Board(conn), root, state)
                join(conn, root, state, board=Board(conn))
            state_path.write_text(json.dumps(state))
    except Exception:
        pass
