"""``delivery_submit`` — hand a coding request from the main chat to the delivery board.

The main chat can run on a cheap model: it only classifies the request and calls
this tool. Card creation, routing, and the chat subscription are deterministic.

small: ONE card. The implementer codes and tests, then hands the SAME card (same
       worktree) to the reviewer through the native review lane. Changes requested
       go straight back to the implementer. No coordinator hop, no extra cards.
large: three chained cards: map (cheap investigator) -> plan (frontier planner, plans from the
       map) -> build (implementer, then the same-card review lane). Nothing waits on a Coordinator.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
# User-submitted work outranks retries of older cards (dispatcher: ORDER BY priority DESC).
PRIORITY = {"small": 20, "large": 10}
MAX_RUNTIME = {"small": "30m", "large": "30m"}  # check_delivery_config.py ceiling

FAST_PATH_BRIEF = """FAST PATH: small task. One card, one worktree, one implementer session.
token_budget: low.

REQUEST (from the user, verbatim):
{request}

IMPLEMENTER
1. Read only the code this change touches. No separate exploration, planning, or dependency cards.
2. Add or adjust a focused test first when the repo has tests for this area, then implement.
3. Run the focused tests plus lint/typecheck for touched files. Commit on this card's branch.
4. If a check cannot run because the environment lacks a tool, dependency, config, or secret,
   record the exact gap in your summary and continue (READY_WITH_RISK). Do not block for it.
   The OCR gate belongs to the reviewer: do not run it and never block on it.
5. Hand this SAME card to review: `hermes kanban request-review <this card id> --reviewer {reviewer}
   --summary "<changed files; commands run and results; commit sha; any READY_WITH_RISK gaps>"`.
6. If review requests changes, fix them on this card and request review again.
7. Block only for a decision only the user can make, and state the exact question.

REVIEWER (fast review, same worktree)
- Review `git diff <base>...HEAD` in this card's worktree against the REQUEST. One pass: list every
  finding at once with file:line.
- REQUEST_CHANGES only for correctness, security, data-loss, or a missed requirement.
  Style and naming go in the approval as notes. Missing tools are noted, not blockers.
- On re-review, check only the delta since the last reviewed commit plus the prior findings.
"""

MAP_BRIEF = """REPOSITORY MAP for a planner. Read-only: no edits, no commits, no plan.
token_budget: low.

REQUEST (from the user, verbatim):
{request}

Read only the code this request touches, then complete this card with the map (FILES, SYMBOLS,
CALLERS, TESTS, COMMANDS, CONVENTIONS, GAPS; about 4 KB, every path:line from a file you opened).
The planner card waiting on this one plans from your map instead of exploring the repo itself.
"""

PLAN_BRIEF = """LARGE TASK: plan from the repository map. Do not load skills.
token_budget: medium.

REQUEST (from the user, verbatim):
{request}

The parent card's result is a repository map made by a cheaper model (kanban_show -> parents).
Treat its FILES/SYMBOLS/TESTS/COMMANDS as your evidence. Open a file only to confirm a line you
cite or to close an item under GAPS, and name that item. No broad searches, no web.

Produce the implementation plan: acceptance criteria, non-goals, risk tier, and ordered slices
with exact files and tests. Complete this card with the FULL plan as the result: the implementer
card waiting on this one reads it from there and has no other copy.
Block only for a decision only the user can make, and state the exact question.
"""

BUILD_BRIEF = """IMPLEMENT THE APPROVED PLAN. One card, one worktree, one implementer session.
token_budget: low.

REQUEST (from the user, verbatim):
{request}

The plan is the parent card's result (kanban_show -> parents). Follow its slices in order; do not
re-plan or re-explore beyond the files it names. Then follow the fast path:
""" + FAST_PATH_BRIEF.split("IMPLEMENTER\n", 1)[1]


def _roster_gaps():
    path = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "roster_gaps.py"
    spec = importlib.util.spec_from_file_location("roster_gaps", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _role(role: str) -> str:
    """Profile mapped to a workflow role in roster.yaml (same parser as resolve_role.py)."""
    roster = HERMES_HOME / "roster.yaml"
    in_roles = False
    for line in roster.read_text().splitlines():
        if line.strip() == "roles:":
            in_roles = True
        elif in_roles and line.startswith(" ") and ":" in line:
            name, profile = line.strip().split(":", 1)
            if name == role:
                return profile.split("#", 1)[0].strip()
    raise KeyError(f"role '{role}' is not mapped in {roster}")


def _hermes(*args: str) -> subprocess.CompletedProcess:
    exe = os.environ.get("HERMES_BIN") or shutil.which("hermes") or "hermes"
    return subprocess.run([exe, *args], capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL)


def _subscribe_calling_chat(task_id: str) -> bool:
    """Route the card's progress/blocks/completion back to the chat that submitted it."""
    try:
        from hermes_cli import kanban_db_notify
        from hermes_cli.kanban_db_connect import connect_closing
        from tools.kanban_tools import _resolve_notify_target  # same target native kanban_create uses
        target = _resolve_notify_target()
        if not target:
            return False
        with connect_closing() as conn:
            kanban_db_notify.add_notify_sub(conn, task_id=task_id, **target)
        return True
    except Exception:
        return False


def build_card(title: str, request: str, project: str, size: str) -> tuple[list[str], str]:
    """(`hermes kanban create` argv, assignee) for a submission. Pure, for testing."""
    reviewer, implementer = _role("reviewer"), _role("implementer")
    if size == "small":
        assignee = implementer
        body = FAST_PATH_BRIEF.format(request=request.strip(), reviewer=reviewer)
    else:  # map first on the cheap investigator; submit() chains plan + build on it
        assignee = _role("investigator")
        title = f"Map: {title}"
        body = MAP_BRIEF.format(request=request.strip())  # chained_card() adds plan + build
    argv = ["kanban", "create", title, "--assignee", assignee, "--body", body,
            "--project", project, "--workspace", "worktree",
            "--priority", str(PRIORITY[size]), "--max-runtime", MAX_RUNTIME[size],
            "--created-by", "delivery_submit", "--json"]
    return argv, assignee


def chained_card(stage: str, title: str, request: str, project: str, parent: str) -> tuple[list[str], str]:
    """Card that starts only after `parent` is done: stage 'plan' (planner) or 'build' (implementer)."""
    if stage == "plan":
        assignee, title = _role("planner"), f"Plan: {title}"
        body = PLAN_BRIEF.format(request=request.strip())
    else:
        assignee = _role("implementer")
        body = BUILD_BRIEF.format(request=request.strip(), reviewer=_role("reviewer"))
    return ["kanban", "create", title, "--assignee", assignee, "--body", body,
            "--project", project, "--workspace", "worktree", "--parent", parent,
            "--priority", str(PRIORITY["large"]), "--max-runtime", MAX_RUNTIME["large"],
            "--created-by", "delivery_submit", "--json"], assignee


def submit(args: dict, **_kw) -> str:
    title = (args.get("title") or "").strip()
    request = (args.get("request") or "").strip()
    project = (args.get("project") or "").strip()
    size = args.get("size") or "small"
    if not (title and request and project) or size not in PRIORITY:
        return json.dumps({"ok": False, "error": "title, request, project are required; size is small|large"})
    rg = _roster_gaps()
    gaps = rg.roster_gaps(home=HERMES_HOME)  # every required role, so the team is never half-staffed
    if gaps:
        return json.dumps({"ok": False, "error": "Can't start: required roles have no working bot. "
                           "Tell the user exactly which ones and how to fix them; no card was created.",
                           "missing_roles": {r: f"{why} — fix: {rg.fix_hint(r)}" for r, why in gaps.items()}})
    try:
        argv, assignee = build_card(title, request, project, size)
    except (OSError, KeyError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    task_id, error = _create(argv)
    if error:
        return error
    result = {"ok": True, "task_id": task_id, "assignee": assignee, "size": size}
    if size == "large":  # map -> plan -> build, each waiting on the previous card
        chain = {"map": task_id}
        for stage in ("plan", "build"):
            argv, _ = chained_card(stage, title, request, project, task_id)
            task_id, error = _create(argv)
            if error:
                return json.dumps({**json.loads(error), "created_so_far": chain})
            chain[stage] = task_id
        result.update(task_id=task_id, cards=chain)
    cards = result.get("cards", {"card": task_id})
    result.update(
        chat_subscribed=all([_subscribe_calling_chat(tid) for tid in cards.values()]),
        next=("implementer codes + tests, then the reviewer checks the same card; you get a "
              "message on review, block, or completion" if size == "small" else
              "cheap map -> frontier plan -> implementer builds it and hands the same card to "
              "review; you get a message on review, block, or completion"))
    return json.dumps(result)


def _create(argv: list[str]) -> tuple[str | None, str | None]:
    """(task id, None) or (None, error JSON) for one `hermes kanban create`."""
    created = _hermes(*argv)
    if created.returncode != 0:
        projects = _hermes("project", "list").stdout.strip()
        return None, json.dumps({"ok": False, "error": (created.stderr or created.stdout).strip()[-800:],
                                 "known_projects": projects})
    try:
        task_id = json.loads(created.stdout).get("id")
    except (json.JSONDecodeError, AttributeError):
        task_id = None
    if not task_id:
        return None, json.dumps({"ok": False, "error": f"unexpected create output: {created.stdout[-400:]}"})
    return task_id, None


SCHEMA = {
    "type": "function",
    "function": {
        "name": "delivery_submit",
        "description": (
            "Hand a coding task in one of the user's repositories to the software-delivery team, "
            "instead of doing it in this chat. size=small for a clear change touching a few files "
            "(bug fix, small feature, refactor of one module): one implementer session plus an "
            "independent review of the same card, usually minutes. size=large for multi-module, "
            "unclear, schema/API/auth/security, or multi-step work: a cheap mapper reads the repo, then "
            "a frontier planner plans from that map. "
            "Returns the card id; progress and the result come back to this chat."),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short imperative card title"},
                "request": {"type": "string", "description": "The user's request verbatim, plus any acceptance criteria they gave"},
                "project": {"type": "string", "description": "Hermes project slug (see `hermes project list`), e.g. climaterx"},
                "size": {"type": "string", "enum": ["small", "large"]},
            },
            "required": ["title", "request", "project", "size"],
        },
    },
}
