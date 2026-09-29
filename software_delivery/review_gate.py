"""``pre_tool_call`` gate on completing a same-card-review delivery card.

1. The implementer cannot complete its own card: a build/fix/content card finishes only through the
   reviewer (``request-review``), so no push ships unreviewed.
2. When the reviewer approves, a pending ``Verify:`` child goes to the release engineer instead of
   the verifier if the diff touches CI, containers, deploy config, dependencies, or migrations.
3. A ``Plan:`` card completes only with a plan: the build card reads its result, so a plan left in
   the summary is copied into the result, and a completion with no plan at all is refused.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess

from . import submit, workspace_prep

MARKER = "REVIEWER (same worktree)"  # every build, fix, and content brief carries it
PLAN_MARKER = "Produce the implementation plan in the PLAN FORMAT"  # both plan briefs, never the build brief
COMPLETE_CMD = re.compile(r"\bkanban\s+complete\s+(t_[0-9a-f]+)")
PLATFORM_PATH = re.compile(
    r"(^|/)(\.github/|\.gitlab-ci|\.circleci/|Dockerfile|docker-compose|compose\.ya?ml$|Procfile$|"
    r"nginx|helm/|k8s/|terraform/|infra/|deploy/|migrations?/|\.env\.example$|\.nvmrc$|"
    r"package(-lock)?\.json$|yarn\.lock$|pnpm-lock\.yaml$|requirements[^/]*\.txt$|pyproject\.toml$|"
    r"poetry\.lock$|uv\.lock$|Gemfile(\.lock)?$|go\.(mod|sum)$|Cargo\.(toml|lock)$)")


def gate(tool_name: str = "", args: dict | None = None, **_kw):
    args = args or {}
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
        if row["assignee"] == submit._role("implementer"):
            return {"action": "block", "message": (
                f"{tid} is reviewed on the same card: you cannot complete it yourself. Push, then run "
                f"`hermes kanban request-review {tid} --reviewer {submit._role('reviewer')} --summary \"<PR url; "
                "changed files; commands run and results; commit sha>\"`. Only the reviewer completes it.")}
        if row["assignee"] == submit._role("reviewer"):
            route_verify(tid, row["workspace_path"], row["project_id"])
    except Exception:
        return None  # never wedge a completion on a gate bug
    return None


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
    base = workspace_prep.project_profile(project, submit.HERMES_HOME).get("base_branch")
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
