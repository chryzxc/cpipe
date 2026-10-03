"""``pre_tool_call`` gate on completing a same-card-review delivery card.

1. The implementer cannot complete its own card: a build/fix/content card finishes only through the
   reviewer (``request-review``), so no push ships unreviewed. A fix round that committed nothing is
   the exception: nothing changed to review, so its result (a repro, a local URL) reaches the user now.
2. When the reviewer approves, a pending ``Verify:`` child goes to the release engineer instead of
   the verifier if the diff touches CI, containers, deploy config, dependencies, or migrations.
3. A ``Plan:`` card completes only with a plan: the build card reads its result, so a plan left in
   the summary is copied into the result, and a completion with no plan at all is refused.
4. The implementer cannot block a card to ask for commit approval: committing and pushing its own
   branch needs none, and each such block cost a whole extra run.
5. A kanban worker cannot start another agent (`hermes chat`, `codex exec`, `claude -p`): a nested
   consult cold-starts a whole agent and stalled one fix round about 15 minutes.
6. The implementer's review request runs the operator's quality checks first (``quality.py``); a failure
   refuses it with the output, so the reviewer never spends a round on what a command finds.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess

from . import quality, submit, workspace_prep

MARKER = "REVIEWER (same worktree)"  # every build, fix, and content brief carries it
PLAN_MARKER = "Produce the implementation plan in the PLAN FORMAT"  # both plan briefs, never the build brief
MAX_REWORK = 2  # request-changes rounds per card before the reviewer must block for the user
REVIEW_CMD = re.compile(r"\bkanban\s+request-review\s+(t_[0-9a-f]+)")
CHANGES_CMD = re.compile(r"\bkanban\s+request-changes\s+(t_[0-9a-f]+)")
COMPLETE_CMD = re.compile(r"\bkanban\s+complete\s+(t_[0-9a-f]+)")
BLOCK_CMD = re.compile(r"\bkanban\s+block\b(.*?)\b(t_[0-9a-f]+)\b(.*)", re.S)
COMMIT_ASK = re.compile(r"COMMIT_READY|\b(authori[sz]|approv|validat|permission|sign.?off)\w*\b[^.\n]{0,80}\bcommit"
                        r"|\bcommit\w*\b[^.\n]{0,80}\b(authori[sz]|approv|validat|permission|sign.?off)", re.I)
NESTED_AGENT = re.compile(r"\bhermes\b[^|;&\n]*\bchat\b|\bcodex\s+exec\b|\bclaude\s+(-p|--print)\b")
PLATFORM_PATH = re.compile(
    r"(^|/)(\.github/|\.gitlab-ci|\.circleci/|Dockerfile|docker-compose|compose\.ya?ml$|Procfile$|"
    r"nginx|helm/|k8s/|terraform/|infra/|deploy/|migrations?/|\.env\.example$|\.nvmrc$|"
    r"package(-lock)?\.json$|yarn\.lock$|pnpm-lock\.yaml$|requirements[^/]*\.txt$|pyproject\.toml$|"
    r"poetry\.lock$|uv\.lock$|Gemfile(\.lock)?$|go\.(mod|sum)$|Cargo\.(toml|lock)$)")


def gate(tool_name: str = "", args: dict | None = None, **_kw):
    args = args or {}
    if (tool_name == "terminal" and os.environ.get("HERMES_KANBAN_TASK")
            and NESTED_AGENT.search(str(args.get("command") or ""))):
        return {"action": "block", "message": (
            "Do not start another agent from a kanban card: it cold-starts a whole agent and stalls the card. "
            "Make the judgment yourself from the code, or block the card with the exact question for the user.")}
    if tool_name == "kanban_request_review":
        return quality_gate(args.get("task_id") or os.environ.get("HERMES_KANBAN_TASK"))
    if tool_name == "terminal" and (m := REVIEW_CMD.search(str(args.get("command") or ""))):
        return quality_gate(m[1])
    if tool_name == "kanban_request_changes":
        return rework_cap_gate(args.get("task_id") or os.environ.get("HERMES_KANBAN_TASK"))
    if tool_name == "terminal" and (m := CHANGES_CMD.search(str(args.get("command") or ""))):
        return rework_cap_gate(m[1])
    if tool_name == "kanban_block":
        return commit_ask_gate(args.get("task_id") or os.environ.get("HERMES_KANBAN_TASK"), str(args.get("reason") or ""))
    if tool_name == "terminal" and (m := BLOCK_CMD.search(str(args.get("command") or ""))):
        return commit_ask_gate(m[2], m[1] + m[3])
    if tool_name == "kanban_complete":
        tid = args.get("task_id") or os.environ.get("HERMES_KANBAN_TASK")
    elif tool_name == "terminal" and (m := COMPLETE_CMD.search(str(args.get("command") or ""))):
        tid = m[1]
    else:
        return None
    try:
        row = _task(tid)
        if row and PLAN_MARKER in (row["body"] or "") and tool_name == "kanban_complete":
            return plan_gate(tid, args)
        if not row or MARKER not in (row["body"] or ""):
            return None
        if row["assignee"] == submit._role("implementer") and not _nothing_to_review(row):
            return {"action": "block", "message": (
                f"{tid} is reviewed on the same card: you cannot complete it yourself. Push, then run "
                f"`hermes kanban request-review {tid} --reviewer {submit._role('reviewer')} --summary \"<PR url; "
                "changed files; commands run and results; commit sha>\"`. Only the reviewer completes it.")}
        if row["assignee"] == submit._role("reviewer"):
            route_verify(tid, row["workspace_path"], row["project_id"])
    except Exception:
        return None  # never wedge a completion on a gate bug
    return None


def _nothing_to_review(row) -> bool:
    """A fix round that committed nothing (it ran, reproduced, or set something up for the user): the PR
    is unchanged, so a review would re-check the same code. The implementer completes it with its result."""
    m = re.search(r"^FIX BASE: ([0-9a-f]{7,40}) ", row["body"] or "", re.M)
    head = m and subprocess.run(["git", "-C", row["workspace_path"] or ".", "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
    return bool(head) and head.startswith(m[1])


def quality_gate(tid: str | None):
    try:
        row = _task(tid)
        if not row or MARKER not in (row["body"] or "") or row["assignee"] != submit._role("implementer"):
            return None
        base = workspace_prep.card_profile(tid, submit._db(), submit.HERMES_HOME).get("base_branch")
        return quality.review_request_gate(tid, row, base, submit.HERMES_HOME)
    except Exception:
        return None  # a broken check setup never wedges a review request


def rework_cap_gate(tid: str | None):
    """After MAX_REWORK rounds, a further request-changes goes to the user instead of a fresh implementer run."""
    if not tid:
        return None
    try:
        conn = sqlite3.connect(f"file:{submit._db()}?mode=ro", uri=True)
        try:
            rounds = conn.execute("SELECT COUNT(*) FROM task_runs WHERE task_id = ? AND outcome = 'changes_requested'",
                                  (tid,)).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return None
    if rounds < MAX_REWORK:
        return None
    return {"action": "block", "message": (
        f"{tid} already went back to the implementer {rounds} times; another rework round restarts it from scratch. "
        f"Do not request changes again: run `kanban_block` on {tid} with your findings as the reason (what is still "
        "wrong, file/line, the fix you expect) so the user decides the next step.")}


def commit_ask_gate(tid: str | None, reason: str):
    if not COMMIT_ASK.search(reason):
        return None
    try:
        row = _task(tid)
    except Exception:
        return None
    if not row or row["assignee"] != submit._role("implementer"):
        return None
    return {"action": "block", "message": (
        f"{tid}: committing and pushing this card's branch needs nobody's approval (only merge, deploy, "
        f"force-push, and credential changes do), whatever an earlier note said. Commit, push, open or update the "
        f"draft PR, then run `hermes kanban request-review {tid} --reviewer {submit._role('reviewer')} --summary "
        "\"<PR url; changed files; commands run and results; commit sha>\"`. Block only for a product, security, or "
        "destructive decision.")}


def plan_gate(tid: str, args: dict):
    result, summary = str(args.get("result") or ""), str(args.get("summary") or "")
    plan = max(result, summary, key=len)
    missing = [h for h in submit.PLAN_HEADINGS if not re.search(rf"^\W*{re.escape(h)}\b", plan, re.M)]
    if len(plan) < 600 or missing:
        return {"action": "block", "message": (
            f"{tid}: the implementer card reads only this card's result and has no other copy of the plan. "
            f"Complete with the FULL plan in `result`, every PLAN FORMAT heading at the start of a line "
            f"(`none` where it does not apply). Missing: {', '.join(missing) or 'the plan body'}.")}
    if plan is not result:
        return {"action": "modify", "args": {"result": plan}}
    return None


def platform_files(worktree: str | None, base_branch: str | None) -> list[str]:
    if not worktree:
        return []
    git = ["git", "-C", worktree]
    base = None
    for ref in filter(None, [base_branch and f"origin/{base_branch}", "origin/HEAD"]):
        r = subprocess.run([*git, "merge-base", "HEAD", ref], capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            base = r.stdout.strip()
            break
    if not base:
        return []
    names = subprocess.run([*git, "diff", "--name-only", f"{base}..HEAD"],
                           capture_output=True, text=True, timeout=30).stdout.split()
    return [n for n in names if PLATFORM_PATH.search(n)]


def route_verify(tid: str, worktree: str | None, project: str | None) -> str | None:
    """Reassign the pending Verify child to the release engineer when platform files changed."""
    engineer = submit._role("release_engineer")
    conn = sqlite3.connect(f"file:{submit._db()}?mode=ro", uri=True)
    try:
        child = conn.execute(
            "SELECT t.id FROM task_links l JOIN tasks t ON t.id = l.child_id WHERE l.parent_id = ? "
            "AND t.title LIKE 'Verify:%' AND t.status IN ('todo', 'ready') AND t.assignee != ?",
            (tid, engineer)).fetchone()
    finally:
        conn.close()
    if not child:
        return None
    base = workspace_prep.card_profile(tid, submit._db(), submit.HERMES_HOME).get("base_branch")
    touched = platform_files(worktree, base)
    if not touched:
        return None
    submit._hermes("kanban", "assign", child[0], engineer)
    submit._hermes("kanban", "comment", child[0],
                   f"Routed to {engineer}: the change touches platform files ({', '.join(touched[:8])}). "
                   "Run the PLATFORM step of the brief as well.")
    submit._journal("verify.routed_platform", child[0], files=touched[:8])
    return child[0]


def _task(tid: str | None):
    if not tid:
        return None
    conn = sqlite3.connect(f"file:{submit._db()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT assignee, body, workspace_path, project_id FROM tasks WHERE id = ?",
                            (tid,)).fetchone()
    finally:
        conn.close()
