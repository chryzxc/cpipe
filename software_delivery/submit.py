"""``delivery_submit`` — hand a coding request from the main chat to the delivery board.

The main chat can run on a cheap model: it only classifies the request and calls
this tool. Card creation, routing, and the chat subscription are deterministic.

content: one build card, no plan and no verify: text/copy/markup/style/docs edits need no tests.
small: build card: the implementer codes and tests, then hands the SAME card (same worktree)
       to the reviewer through the native review lane; changes requested go straight back.
large: map (cheap investigator) -> plan (frontier planner, plans from the map) -> build.
Both end in a verify card: the verifier runs the tests in the build worktree. On failure it calls
``delivery_verify_failed``, which opens a fix card in the same worktree plus a fresh verify card,
at most MAX_FIX_ROUNDS times. Nothing waits on a Coordinator.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
# User-submitted work outranks retries of older cards (dispatcher: ORDER BY priority DESC).
PRIORITY = {"content": 30, "small": 20, "large": 10}
MAX_RUNTIME = {"content": "15m", "small": "30m", "large": "30m"}  # check_delivery_config.py ceiling
MAX_FIX_ROUNDS = 2
MAX_REQUEST_CHARS = 3000
DUPLICATE_TITLE_RATIO = 0.85
STAGE_PREFIX = re.compile(r"^(map|plan|verify|fix \d+|review)\s*:\s*", re.I)
REVIEW_TITLE = re.compile(r"^\s*(code[\s-]*)?review\b|^\s*re-?review\b", re.I)
URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/(?:pull|issues)/\d+")

SCOPE = """CONTEXT: this card is your whole assignment. Read only the files it names, their callers,
and the parent card's result (kanban_show -> parents). No broad searches, no web, no skills
beyond your role's, no other cards or conversations. Block only for a decision only the user can
make, and state the exact question.
"""

PIN_BRIEF = """PLAN a small change: what to change and which current behaviors to pin. Read-only.
token_budget: low.

REQUEST (from the user):
{request}

""" + SCOPE + """
Read the code the request touches and its direct callers (for a route, grep its URL path in
server AND client). Complete this card with, in about 2 KB, every path:line from a file you opened:
- CHANGE: the files and functions to edit, and how.
- PINS: each caller behavior the change can reach that must keep working:
  `<caller file:line> — <behavior> — covered by <test>` or `— UNPINNED: <test file> asserts <what>`.
- RED: the test for the requested behavior and why it fails on today's code.
- RISKS: anything the implementer must not break or decide.
PINS cover behavior only: static text, markup, and styles need none. Plan nothing the request
did not ask for (no extra tests, refactors, or hardening).
The implementer card waiting on this one builds from your result and has no other copy.
"""

MAP_BRIEF = """REPOSITORY MAP for a planner. Read-only: no edits, no commits, no plan.
token_budget: low.

REQUEST (from the user):
{request}

""" + SCOPE + """
Read only the code this request touches, then complete this card with the map (FILES, SYMBOLS,
CALLERS, TESTS, UNPINNED (caller behaviors this change reaches that no test covers), COMMANDS,
CONVENTIONS, GAPS; about 4 KB, every path:line from a file you opened).
The planner card waiting on this one plans from your map instead of exploring the repo itself.
"""

PLAN_BRIEF = """LARGE TASK: plan from the repository map. Do not load skills.
token_budget: medium.

REQUEST (from the user):
{request}

""" + SCOPE + """
The parent card's result is a repository map made by a cheaper model. Treat its
FILES/SYMBOLS/TESTS/COMMANDS as your evidence. Open a file only to confirm a line you cite or to
close an item under GAPS, and name that item.

Produce the implementation plan: acceptance criteria, non-goals, risk tier, PINS (as in a small
plan: each reachable caller behavior with its covering test or UNPINNED and the test to add), and
ordered slices with exact files and tests. The first slice adds every UNPINNED test, passing on
today's code. PINS cover behavior only: static text, markup, and styles need none. Plan the
smallest change that meets the request: nothing it did not ask for (no extra tests, refactors,
or hardening). Complete this card with the FULL plan as the result: the implementer card waiting
on this one reads it from there and has no other copy.
"""

IMPLEMENTER = """IMPLEMENTER
1. The parent card's result is the planner's CHANGE/PINS/RED (small) or plan (large). Follow it;
   do not re-plan. Read the files it names; if you find a reachable caller it missed, add it to
   PINS and say so in your summary.
2. PIN, before editing any source file: write each UNPINNED test so it asserts what the code does
   TODAY, and run it: it must PASS on the unchanged code. Commit these first
   (`test: pin current behavior of <area>`).
3. RED/GREEN: write the requested-behavior test and run it: it must FAIL for the reason the
   request describes (RED). Implement until it passes (GREEN) with every pinned test still
   passing. A pinned test may change only where the REQUEST changes that behavior; say so.
4. Run the tests related to every changed file, not only the new ones (`npx jest --findRelatedTests
   <files>`, `npx vitest related --run <files>`, or the tests importing the module), in every
   package the change reaches (server and client), plus lint/typecheck for touched files.
   Commit on this card's branch.
5. If a check cannot run because the environment lacks a tool, dependency, config, or secret,
   record the exact gap in your summary and continue (READY_WITH_RISK). Do not block for it.
   The OCR gate belongs to the reviewer: do not run it and never block on it.
6. Hand this SAME card to review: `hermes kanban request-review <this card id> --reviewer {reviewer}
   --summary "<changed files; PINNED: <test ids>; commands run and results; commit sha;
   any READY_WITH_RISK gaps>"`.
7. If review requests changes, fix them on this card and request review again.

REVIEWER (same worktree)
- Review `git diff <base>...HEAD` in this card's worktree against the REQUEST and the parent
  card's plan. One pass: list every finding at once with file:line.
- Callers: for every changed function, route, API field, event, or component prop, find its users
  (grep the name and, for routes, the URL path across server AND client). Your verdict lists
  `CALLERS CHECKED: <symbol> -> <file:line> safe (<test that proves it>)|broken`. A broken caller
  is REQUEST_CHANGES, and so is a reachable caller with no test proving it still works: ask for a
  pinned test. An approval without this list is not an approval. Static text, markup, links,
  and styles are not behavior: they need no test.
- REQUEST_CHANGES only for correctness, security, data-loss, a missed requirement, or a missing
  pin. Style and naming go in the approval as notes. Missing tools are noted, not blockers.
  Never ask for a test, harness, or file the REQUEST or plan excludes: note it as a follow-up.
- On re-review, check only the delta since the last reviewed commit plus the prior findings.
  A prior finding the implementer could not or would not fix is not a second REQUEST_CHANGES:
  approve with it under RESIDUAL RISK so the user decides. Never request changes twice on one finding.
"""

CONTENT_BRIEF = """CONTENT CHANGE: text, copy, markup, styles, docs, or a config value. No logic.
token_budget: low.

REQUEST (from the user):
{request}

""" + SCOPE + """
IMPLEMENTER
1. Edit only what the request needs. Grep for every place the same content renders (a server
   template AND a client view can both show one page) and update each one the same way.
2. No new tests, pins, or harnesses: static content is not behavior. Run the touched package's
   existing lint/build if it is quick; if it cannot run, note it and continue.
3. Commit on this card's branch (Conventional Commit), then hand this SAME card to review:
   `hermes kanban request-review <this card id> --reviewer {reviewer} --summary "<files; what
   text changed; checks run; commit sha>"`.
4. If the change needs logic (a route, handler, state, API field), block with
   "NEEDS size=small: <why>" instead of building it.

REVIEWER (same worktree)
- Review `git diff <base>...HEAD` against the REQUEST only: required wording (exact where the
  request quotes it), spelling and branding, broken markup or links, every place the content
  renders, and files outside the request. No tests, pins, harnesses, OCR, or caller traces:
  a content change has none.
- One pass, every finding at once. Approve (`hermes kanban complete`) or REQUEST_CHANGES only
  for wrong or missing required text, broken markup/links, or out-of-scope files.
- On re-review check only your prior findings. A finding already raised once goes in the
  approval as a note for the user; never request changes twice on it.
"""

BUILD_BRIEF = """IMPLEMENT the planned change. One card, one worktree, one implementer session.
token_budget: low.

REQUEST (from the user):
{request}

""" + SCOPE + "\n" + IMPLEMENTER

VERIFY_BRIEF = """VERIFY the reviewed change by running it. No edits or commits; step 1 checks files out and restores them.
token_budget: low.

REQUEST (from the user):
{request}

Work in the parent card's worktree (kanban_show -> parents -> workspace; the workspace note tells
you when you start elsewhere). BASE = `git merge-base HEAD <base branch from the parent's review
handoff, else origin's default branch>`.
1. PROOF: `git diff --name-only BASE..HEAD`. Restore the changed NON-test files to BASE
   (`git checkout BASE -- <files>`), run the changed/new tests: at least one requested-behavior
   test must FAIL, and every test the review handoff lists as PINNED must PASS (a pin failing at
   BASE was written for the new code: FAIL "pin does not assert current behavior"). Then
   `git checkout HEAD -- <files>` and confirm `git status --porcelain` is empty.
   No changed tests for a behavior change is a FAIL ("no test proves the change").
2. RELATED: run the tests related to every changed file in each package the change reaches
   (`npx jest --findRelatedTests`, `npx vitest related --run`, or the importing tests). All pass.
3. SUITE: run the repo's CI test and lint commands (.github/workflows, package.json scripts) for
   the touched packages. A failure counts only if it also passes on BASE (re-run that test at BASE
   the same way as step 1). Skip a command that needs a secret or service; name it.
PASS: complete this card with each command and its result.
FAIL: call `delivery_verify_failed` with this card id and the exact failures (command, test,
error), then complete this card with the same evidence. If that tool says the round limit is
reached, block this card with the failures instead.
"""

FIX_BRIEF = """FIX ROUND {round}: the verifier ran the reviewed change and it failed.
token_budget: low.

REQUEST (from the user):
{request}

FAILURES (from the verifier):
{failures}

You are in the same worktree and branch as the original change. Fix these failures only, then
follow steps 3-7 (PINNED tests must still pass):
""" + SCOPE + "\n" + IMPLEMENTER


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
    if size == "content":  # straight to the implementer; same-card review, no plan or verify
        assignee = _role("implementer")
        body = CONTENT_BRIEF.format(request=request.strip(), reviewer=_role("reviewer"))
    elif size == "small":  # the frontier planner picks the change and the pins; submit() chains build
        assignee, title = _role("planner"), f"Plan: {title}"
        body = PIN_BRIEF.format(request=request.strip())
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
    """Card that starts only after `parent` is done: 'plan' (planner), 'build' (implementer), or
    'verify' (verifier, scratch workspace; the workspace_prep hook sends it to the parent worktree)."""
    workspace = "worktree"
    if stage == "plan":
        assignee, title = _role("planner"), f"Plan: {title}"
        body = PLAN_BRIEF.format(request=request.strip())
    elif stage == "verify":
        assignee, title, workspace = _role("verifier"), f"Verify: {title}", "scratch"
        body = VERIFY_BRIEF.format(request=request.strip())
    else:
        assignee = _role("implementer")
        body = BUILD_BRIEF.format(request=request.strip(), reviewer=_role("reviewer"))
    return ["kanban", "create", title, "--assignee", assignee, "--body", body,
            "--project", project, "--workspace", workspace, "--parent", parent,
            "--priority", str(PRIORITY["large"]), "--max-runtime", MAX_RUNTIME["large"],
            "--created-by", "delivery_submit", "--json"], assignee


def _bare(title: str) -> str:
    while STAGE_PREFIX.match(title):
        title = STAGE_PREFIX.sub("", title, count=1)
    return " ".join(title.lower().split())


def find_duplicate(title: str, request: str, project: str) -> str | None:
    """An open card for the same work: same GitHub issue/PR URL, or same project and a near-identical title."""
    urls = set(URL_RE.findall(request))
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        rows = conn.execute("SELECT id, title, body, project_id FROM tasks WHERE status NOT IN "
                            "('done','archived') ORDER BY created_at").fetchall()
        conn.close()
    except sqlite3.Error:
        return None
    bare = _bare(title)
    for tid, other_title, body, other_project in rows:
        if urls & set(URL_RE.findall(body or "")):
            return tid
        if other_project == project and difflib.SequenceMatcher(
                None, bare, _bare(other_title or "")).ratio() >= DUPLICATE_TITLE_RATIO:
            return tid
    return None


def _journal(kind: str, card: str | None = None, **detail) -> None:
    try:
        from . import status
        status._journal(kind, card, **detail)
    except Exception:
        pass


def submit(args: dict, **_kw) -> str:
    title = (args.get("title") or "").strip()
    request = (args.get("request") or "").strip()
    project = (args.get("project") or "").strip()
    size = args.get("size") or "small"
    if not (title and request and project) or size not in PRIORITY:
        return json.dumps({"ok": False, "error": "title, request, project are required; size is content|small|large"})
    if len(request) > MAX_REQUEST_CHARS:  # workers get the card, not the chat: keep it a brief
        return json.dumps({"ok": False, "error": f"request is {len(request)} chars; rewrite it as a brief "
                           f"under {MAX_REQUEST_CHARS}: goal, acceptance criteria, files the user named"})
    if REVIEW_TITLE.search(title):  # review happens on the build card's own review lane, never a new card
        return json.dumps({"ok": False, "error": "Don't submit review cards: every build card is reviewed on the "
                           "same card through the review lane. To change an existing card, comment on it or use "
                           "`hermes kanban request-changes <id>`; no card was created."})
    duplicate = None if args.get("force") else find_duplicate(title, request, project)
    if duplicate:
        _journal("waste.duplicate_card", duplicate, title=title[:80], project=project)
        return json.dumps({"ok": False, "duplicate_of": duplicate,
                           "error": f"{duplicate} is already open for this work. Tell the user and follow that "
                           "card (delivery_status); resubmit with force=true only if the user says it is new work."})
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
    conventions = _conventions(project)
    task_id, error = _create(_stamp(argv, conventions))
    if error:
        return error
    result = {"ok": True, "task_id": task_id, "assignee": assignee, "size": size}
    # each stage waits on the previous card; every code path ends in verify, content is one card
    chain = {"large": {"map": task_id}, "small": {"plan": task_id}, "content": {"build": task_id}}[size]
    stages = {"large": ("plan", "build", "verify"), "small": ("build", "verify"), "content": ()}[size]
    for stage in stages:
        argv, _ = chained_card(stage, title, request, project, task_id)
        task_id, error = _create(_stamp(argv, conventions))
        if error:
            return json.dumps({**json.loads(error), "created_so_far": chain})
        chain[stage] = task_id
    result.update(task_id=chain["build"], cards=chain)
    result.update(
        chat_subscribed=all([_subscribe_calling_chat(tid) for tid in chain.values()]),
        next=("implementer edits, reviewer checks the wording on the same card" if size == "content" else
              ("frontier plan (change + pins) -> " if size == "small" else "cheap map -> frontier plan -> ")
              + "implementer pins current behavior and builds, frontier reviewer checks the same card, "
              "verifier runs the tests") + "; you get a message on review, block, or completion")
    return json.dumps(result)


def _conventions(project: str) -> str:
    from . import workspace_prep
    return workspace_prep.profile_brief(workspace_prep.project_profile(project, HERMES_HOME))


def _stamp(argv: list[str], conventions: str) -> list[str]:
    """Append the project's saved conventions to a create argv's --body."""
    if not conventions:
        return argv
    i = argv.index("--body") + 1
    return [*argv[:i], f"{argv[i]}\n\n{conventions}", *argv[i + 1:]]


def verify_failed(args: dict, **_kw) -> str:
    """Verifier found failures: fix card in the build worktree + a fresh verify card after it."""
    verify_id, failures = (args.get("task_id") or "").strip(), (args.get("failures") or "").strip()
    if not (verify_id and failures):
        return json.dumps({"ok": False, "error": "task_id (the verify card) and failures are required"})
    task = _task_row(verify_id)
    if not task:
        return json.dumps({"ok": False, "error": f"unknown card {verify_id}"})
    title = task["title"] or ""
    m = re.match(r"Verify: Fix (\d+): (.*)", title, re.S)
    rounds, base_title = (int(m[1]), m[2]) if m else (0, title.removeprefix("Verify: "))
    if rounds >= MAX_FIX_ROUNDS:
        return json.dumps({"ok": False, "error": f"round limit ({MAX_FIX_ROUNDS}) reached: block your card "
                           "with the failures so the user decides"})
    worktree = _parent_worktree(verify_id)
    if not worktree:
        return json.dumps({"ok": False, "error": "no parent worktree found; block your card with the failures"})
    request = (task["body"] or "").split("REQUEST (from the user):\n", 1)[-1].split("\n\nWork in", 1)[0]
    project = task["project_id"] or ""
    fix_title = f"Fix {rounds + 1}: {base_title}"
    fix_argv = ["kanban", "create", fix_title, "--assignee", _role("implementer"),
                "--body", FIX_BRIEF.format(round=rounds + 1, request=request, failures=failures,
                                           reviewer=_role("reviewer")),
                *(["--project", project] if project else []), "--workspace", f"dir:{worktree}",
                "--parent", verify_id, "--priority", str(PRIORITY["small"]),
                "--max-runtime", MAX_RUNTIME["small"], "--created-by", "delivery_submit", "--json"]
    fix_id, error = _create(fix_argv)
    if error:
        return error
    argv, _ = chained_card("verify", fix_title, request, project, fix_id)
    reverify_id, error = _create(argv)
    if error:
        return json.dumps({**json.loads(error), "created_so_far": {"fix": fix_id}})
    for tid in (fix_id, reverify_id):
        _copy_subscriptions(verify_id, tid)
    return json.dumps({"ok": True, "fix": fix_id, "verify": reverify_id, "round": rounds + 1,
                       "next": "complete your verify card with the failure evidence"})


def _db() -> Path:
    return Path(os.environ.get("HERMES_KANBAN_DB") or HERMES_HOME / "kanban.db")


def _task_row(task_id: str):
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT title, body, project_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        return row
    except sqlite3.Error:
        return None


def _parent_worktree(task_id: str):
    from . import workspace_prep
    return workspace_prep.parent_worktree(task_id, _db())


def _copy_subscriptions(src: str, dst: str) -> None:
    """Whoever follows the verify card (the submitting chat) also hears about its fix cards."""
    try:
        from hermes_cli import kanban_db_notify
        from hermes_cli.kanban_db_connect import connect_closing
        keep = ("platform", "chat_id", "thread_id", "user_id", "user_id_alt", "chat_type",
                "notifier_profile", "delivery_mode")
        with connect_closing() as conn:
            for sub in kanban_db_notify.list_notify_subs(conn, src, include_unowned=True):
                kanban_db_notify.add_notify_sub(conn, task_id=dst, **{k: sub.get(k) for k in keep})
    except Exception:
        pass


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


VERIFY_FAILED_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delivery_verify_failed",
        "description": ("Verifier only: the change failed verification. Opens a fix card for the "
                        "implementer in the same worktree and a new verify card after it."),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Your verify card id"},
                "failures": {"type": "string", "description": "Each failing command, test, and error"},
            },
            "required": ["task_id", "failures"],
        },
    },
}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "delivery_submit",
        "description": (
            "Hand a coding task in one of the user's repositories to the software-delivery team, "
            "instead of doing it in this chat. size=content when only text, copy, markup, styles, docs, "
            "or a config value change (add/edit a page section, legal wording, labels): one implementer "
            "session plus a wording review, no plan, no tests. size=small for a clear change touching a few files "
            "(bug fix, small feature, refactor of one module): one implementer session plus an "
            "independent review of the same card, usually minutes. size=large for multi-module, "
            "unclear, schema/API/auth/security, or multi-step work: a cheap mapper reads the repo, then "
            "a frontier planner plans from that map. "
            "Returns the card id; progress and the result come back to this chat."),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short imperative card title"},
                "request": {"type": "string", "description": "A brief, not the conversation: the goal, acceptance criteria, and any file, route, or area the user named. Workers see only this. Max 3000 chars."},
                "project": {"type": "string", "description": "Hermes project slug (see `hermes project list`), e.g. my-app"},
                "size": {"type": "string", "enum": ["content", "small", "large"]},
                "force": {"type": "boolean", "description": "Only when the user confirmed this is new work although an open card looks the same"},
            },
            "required": ["title", "request", "project", "size"],
        },
    },
}
