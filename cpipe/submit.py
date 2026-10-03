"""``delivery_submit`` — hand a coding request from the main chat to the delivery board.

The main chat can run on a cheap model: it only classifies the request and calls
this tool. Card creation, routing, and the chat subscription are deterministic.

content: one build card, no plan: text/copy/markup/style/docs edits need no tests.
small: build: the implementer plans inline (a plan comment, no planner card), codes,
       tests, pushes a draft PR, then hands the SAME card (same worktree) to the reviewer through the
       native review lane; changes requested go straight back. The operator tests the PR, then merges.
large: map (cheap investigator) -> plan (frontier planner, plans from the map) -> build.
small/large (and fix_of) get a verify card unless verify=false: the verifier runs the tests in the
build worktree (review_gate hands it to the release engineer when platform files changed). On failure it
calls ``delivery_verify_failed``, which opens a fix card in the same worktree plus a fresh verify
card, at most MAX_FIX_ROUNDS times.
fix_of=<card or PR>: the operator found a problem while testing; one fix card in that card's
worktree and branch, reviewed on the same card and then verified, so the open PR updates.
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
from .home import root as _root_home

HERMES_HOME = _root_home()
# User-submitted work outranks retries of older cards (dispatcher: ORDER BY priority DESC).
PRIORITY = {"content": 30, "small": 20, "large": 10}
# a whole feature is one session: a timeout kills it mid-build and the retry starts cold
LIVE = ("todo", "ready", "running", "review", "scheduled")  # blocked/triage wait on the user: not live
MAX_RUNTIME = {"content": "25m", "small": "60m", "large": "60m", "map": "20m", "plan": "20m", "build": "60m"}
MAX_FIX_ROUNDS = 2
MAX_REQUEST_CHARS = 3000
DUPLICATE_TITLE_RATIO = 0.85
STAGE_PREFIX = re.compile(r"^(map|plan|verify|fix \d+|review)\s*:\s*", re.I)
REVIEW_TITLE = re.compile(r"^\s*(code[\s-]*)?review\b|^\s*re-?review\b", re.I)
URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/(?:pull|issues)/\d+")

SCOPE = """CONTEXT: this card is your whole assignment: the REQUEST, the code it touches and its
callers, and the parent card's result (kanban_show -> parents). No broad searches, no web, no
skills beyond your role's, no other cards or conversations.

FIND CODE: if the repo has `.codegraph/`, start with `codegraph explore "<symbols, files, or question>"`
in the terminal: one call returns the relevant source plus its callers. Use search_files/read_file only
for what it did not return. Read a file once; afterwards re-read only the lines you changed.

FINISH, DON'T STOP. Without asking you may: install dependencies (`npm ci`/`npm install`, and
commit a lockfile change the build needs), run any test, lint, build, or local dev command, commit
on this card's branch, push this card's branch, and open or update its draft PR. Change any file
the REQUEST needs, including files a plan did not name: list those under OUT_OF_PLAN in your
summary. Never force-push or rewrite pushed history, merge, deploy, touch production, or change
credentials. Never create kanban cards: findings, follow-ups, and questions go in your summary or
the PR. Block only for a decision only the user can make (product behavior, security policy, a
destructive or external action): state the exact question with 2-4 options, recommended first.
"""

# One plan shape for the planner card and the implementer's own plan; review_gate checks the headings.
PLAN_FORMAT = """PLAN FORMAT: every heading below, in this order, at the start of a line. Write `none` under a
heading that does not apply; never drop it. Every file reference is path:line from a file you opened.
GOAL: one sentence.
ACCEPTANCE: AC1..n, each something a person or a test can observe.
NON-GOALS: only what the REQUEST excludes, quoted.
ENTRY POINTS: each way users or systems reach the change (direct URL, in-app navigation/router,
  API client, job, another package) -> path:line -> IN SCOPE, or NON-GOAL with the REQUEST's words.
  Never drop one by assumption: check how users actually get there.
CONTRACTS: each value crossing a boundary: sender path:line field -> receiver path:line field ->
  agree | MISMATCH (fixed in slice N). Client payload, route, model/DB, response.
ADD: each new file, function, route, component: purpose, slice N. (NEW: tests after the code.)
CHANGE: each existing symbol path:line: what changes, slice N. (MODIFIED: pinned first.)
DON'T TOUCH: files that look related but must not change, and why.
PINS: each caller behavior a CHANGE reaches -> covered by <test> | UNPINNED -> <test to add>.
TESTS: each test file -> copies harness <existing test that already mounts this component or calls
  this route> -> asserts <observable effect> -> command. Behavior only: mount/render or call real
  code, never read source text. No harness in the repo: say so and add the smallest real one as a slice.
CHECK: each command to run after the build (related tests in every package, lint, typecheck,
  build) and the result that counts as pass.
RISKS: what could regress, where, and which test or CHECK catches it.
OPEN DECISIONS: product questions the code cannot answer, with 2-4 options, recommended first; or none.
SLICES: ordered; each: files, tests, done-when.
"""
PLAN_TASK = """Produce the implementation plan in the PLAN FORMAT below. The first slice adds every UNPINNED
test, passing on today's code. If an OPEN DECISION changes what gets built, block this card with it
instead of guessing. PINS cover behavior only: static text, markup, and styles need none. Plan the
smallest change that meets the request: nothing it did not ask for (no extra tests, refactors,
or hardening). Complete this card with the FULL plan in `result` (not only the summary): the
implementer card waiting on this one reads it from there and has no other copy.

""" + PLAN_FORMAT
PLAN_HEADINGS = re.findall(r"^([A-Z][A-Z' -]+):", PLAN_FORMAT.split("\n", 2)[2], re.M)

MAP_BRIEF = """REPOSITORY MAP for a planner. Read-only: no edits, no commits, no plan.
token_budget: low.

REQUEST (from the user):
{request}

""" + SCOPE + """
Read only the code this request touches, then complete this card with the map (FILES, SYMBOLS,
CALLERS, ENTRY POINTS (every way a user or system reaches this behavior or content: direct URL,
in-app router/navigation, API clients, jobs, other packages that render or call it), CONTRACTS
(field names and shapes at each handoff: client payload -> route -> model/DB -> response),
TESTS (plus HARNESS: an existing test that already mounts this kind of component or calls this kind
of route, and its command), UNPINNED (caller behaviors this change reaches that no test covers),
COMMANDS, CONVENTIONS, GAPS; about 4 KB, every path:line from a file you opened).
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

""" + PLAN_TASK

IMPLEMENTER = """IMPLEMENTER
1. PLAN. If a parent card's result is a plan, follow it; do not re-plan. Otherwise plan in one
   pass yourself: read the code the REQUEST touches and its direct callers (for a route, grep its
   URL path in server AND client; for content or a page, every place it renders and every way users
   navigate to it), then post the plan as a card comment in the PLAN FORMAT at the end of this
   card, one line per item (a two-file change fits in about 1.5 KB). NEW = a new file, function,
   route, or component nothing existing calls yet; MODIFIED = existing code whose behavior callers
   rely on. Add any reachable caller a parent plan missed to PINS. Plan nothing the REQUEST did not
   ask for.
2. PIN, MODIFIED code only, before editing it: write each UNPINNED test so it asserts what the
   code does TODAY, and run it: it must PASS on the unchanged code. Commit these first
   (`test: pin current behavior of <area>`). Pure NEW code skips this step: nothing to break.
3. MODIFIED: write the requested-behavior test and run it: it must FAIL for the reason the request
   describes (RED); implement until it passes (GREEN) with every pinned test still passing. A
   pinned test may change only where the REQUEST changes that behavior; say so.
   NEW: write the code first, then tests for its behavior (test-after is fine; no RED needed).
4. Run the tests related to every changed file, not only the new ones (`npx jest --findRelatedTests
   <files>`, `npx vitest related --run <files>`, or the tests importing the module), in every
   package the change reaches (server and client), plus lint/typecheck for touched files.
   If `--listTests` shows more than 20 related suites, that is the full suite: run only the tests
   that import a changed file directly and leave the rest to the PR's CI.
   A test command that hits the terminal timeout is hung, not slow: never rerun it unchanged. Add
   `--forceExit` (jest) or run fewer files; if it still hangs, record it as READY_WITH_RISK.
   Commit on this card's branch and push it (`git push -u origin HEAD`). On the first push open a
   draft PR against the project's base branch (`gh pr create --draft`). The title and body describe
   the whole branch (`git diff <base>...HEAD`), never only the latest commit: after every later
   push, rewrite them with `gh pr edit <url> --title ... --body ...` to cover all the changes so
   far. The body ends with `## How to test`: 3-6 manual steps a person follows to see the whole
   change work, and what could regress. If push or the PR fails, record the exact error as
   READY_WITH_RISK.
5. If a check cannot run because the environment lacks a tool, dependency, config, or secret,
   record the exact gap in your summary and continue (READY_WITH_RISK). Do not block for it.
   The OCR gate belongs to the reviewer: do not run it and never block on it.
6. Hand this SAME card to review: `hermes kanban request-review <this card id> --reviewer {reviewer}
   --summary "<PR url; changed files; OUT_OF_PLAN files; PINNED: <test ids>; commands run and
   results; commit sha; any READY_WITH_RISK gaps>"`.
7. If review requests changes, fix them on this card, push, and request review again.

REVIEWER (same worktree)
- Review `git diff <base>...HEAD` in this card's worktree against the REQUEST and the plan (the
  parent card's result, or the implementer's plan comment). One pass: list every finding at once
  with file:line. Walk the plan: every ACCEPTANCE item met, every IN SCOPE ENTRY POINT works, every
  CONTRACTS mismatch fixed, every TESTS line exists and exercises real code, and no changed file
  outside ADD/CHANGE without an OUT_OF_PLAN note. A plan item skipped silently is a missed requirement.
- Callers: for every changed function, route, API field, event, or component prop, find its users
  (grep the name and, for routes, the URL path across server AND client). Your verdict lists
  `CALLERS CHECKED: <symbol> -> <file:line> safe (<test that proves it>)|broken`. A broken caller
  is REQUEST_CHANGES, and so is a reachable existing caller with no test proving it still works:
  ask for a pinned test. NEW code has no existing callers (`CALLERS CHECKED: none (new code)`):
  it needs tests of its own behavior, never pins. An approval without this list is not an
  approval. Static text, markup, links, and styles are not behavior: they need no test.
- Run the related tests yourself at HEAD (never trust the handoff's results) in every package the
  change reaches. A failure that passes at BASE is a regression: REQUEST_CHANGES.
- Tests must exercise behavior: a test that reads source text (readFileSync or a regex over the
  file) or calls a copied/extracted piece of the logic proves nothing. Require a rendered/mounted
  component or a real route/function call asserting the effect: REQUEST_CHANGES.
- Behavior the REQUEST did not ask for (new handlers, refactors, changed defaults, "while I was
  here" hardening) is a finding: revert it, or list it so the user approves it.
- REQUEST_CHANGES only for correctness, security, data-loss, regression, a missed requirement, a
  missing pin or behavior test, or unrequested behavior. Style and naming go in the approval as notes. Missing tools are noted, not blockers.
  Anything the REQUEST needs is in scope even when a plan did not name the file; never ask for a
  test, harness, or file the REQUEST excludes: note it as a follow-up.
- On a `Fix N:` card, review only `git diff <FIX BASE from the card>..HEAD` and the callers of what
  changed there: the earlier rounds were already approved.
- On re-review, check only the delta since the last reviewed commit plus the prior findings.
  An unchanged patch (a rebase or empty push: `git diff $(git merge-base origin/<base> <sha>) <sha> |
  git patch-id --stable` equal for the last reviewed sha and HEAD) keeps the prior verdict: re-run
  the related tests only.
  A prior Medium/Low finding the implementer could not or would not fix is not a second
  REQUEST_CHANGES: approve with it under RESIDUAL RISK. At most 3 review rounds: on the third,
  approve with Medium/Low items under RESIDUAL RISK. RESIDUAL RISK never holds a High (wrong
  behavior, security, data loss, regression): if one is still open, block this card with the
  finding and 2-3 options so the user decides; never approve over it.
- On APPROVED: if kanban_show lists a `Verify:` child card, leave the PR draft (the verifier marks
  it ready once the tests pass); otherwise mark it ready (`gh pr ready <url>`); a failure is a note.
  Never merge. The user tests the PR by hand and merges.
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

BUILD_BRIEF = """IMPLEMENT the REQUEST. One card, one worktree, one implementer session.
token_budget: low.

REQUEST (from the user):
{request}

""" + SCOPE + "\n" + IMPLEMENTER + "\n" + PLAN_FORMAT

VERIFY_BRIEF = """VERIFY the reviewed change by running it. No edits or commits; step 1 checks files out and restores them.
token_budget: low.

REQUEST (from the user):
{request}

FIND CODE: if the repo has `.codegraph/`, start with `codegraph explore "<symbols, files, or question>"`
in the terminal: one call returns the relevant source plus its callers. Use search_files/read_file only
for what it did not return. Read a file once; afterwards re-read only the lines you changed.

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
   If `--listTests` shows more than 20 related suites, that is the full suite: run only the tests
   that import a changed file directly.
   A test command that hits the terminal timeout is hung, not slow: never rerun it unchanged. Add
   `--forceExit` (jest) or run fewer files; if it still hangs, record it as READY_WITH_RISK.
3. NO FULL SUITE: never run the whole test suite; the PR's CI does that. Lint/typecheck only the
   changed files (`npx eslint <files>`, `ruff check <files>`, ...). Report `gh pr checks <url>`
   as it stands (pending is a note, never a wait; a red check counts only if it is not red on BASE).
4. PLATFORM, only if the diff touches CI, containers, deploy config, dependency manifests or
   lockfiles, or migrations: clean install from the lockfile (`npm ci`, `uv sync --frozen`, ...),
   the production build, config syntax checks (`docker compose config`, workflow YAML), and
   migrations up then down on a local/test database. Never deploy or touch a shared environment.
FIX ROUND (this card's title starts `Verify: Fix`): the earlier verify already covered the original
change. Take the sha it verified from its result (kanban_show -> parents -> the fix card -> its
parent), re-run each command it reported failing, and run PROOF and RELATED only for files changed
since that sha (`git diff --name-only <sha>..HEAD`). No browser or live QA unless those files are UI.
LIVE QA you cannot run here (a real device, a browser automation permission, a server, account, or
secret you lack) is not a failure: list it under NOT VERIFIED for the user to check by hand and judge
on what you did run. Never call `delivery_verify_failed` or block for it.
PASS: mark the parent's draft PR ready (`gh pr ready <url>`; a failure is a note), then complete
this card with each command and its result.
FAIL: call `delivery_verify_failed` with this card id and the exact failures (command, test,
error), then complete this card with the same evidence. If that tool says the round limit is
reached, block this card with the failures instead.
"""

FIX_BRIEF = """FIX ROUND {round}: {source}.
token_budget: low.

REQUEST (from the user):
{request}

FAILURES ({reporter}):
{failures}

You are in the same worktree and branch as the original change; its PR updates when you push.
FIX BASE: {fix_base} (HEAD before this round). Files this branch already changes:
{changed}
Start from these files and the FAILURES' file:line; do not re-explore the repo or re-plan.
New requests the user adds while this card waits arrive as comments: handle each in this round.
Fix these failures only, then follow steps 3-7 (PINNED tests must still pass):
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
    elif size == "small":  # straight to the implementer, who plans inline (IMPLEMENTER step 1)
        assignee = _role("implementer")
        body = BUILD_BRIEF.format(request=request.strip(), reviewer=_role("reviewer"))
    else:  # map first on the cheap investigator; submit() chains plan + build on it
        assignee = _role("investigator")
        title = f"Map: {title}"
        body = MAP_BRIEF.format(request=request.strip())  # chained_card() adds plan + build
    argv = ["kanban", "create", title, "--assignee", assignee, "--body", body,
            "--project", project, "--workspace", "worktree",
            "--priority", str(PRIORITY[size]), "--max-runtime", MAX_RUNTIME[size],
            "--created-by", "delivery_submit", "--json"]
    return argv, assignee


def chained_card(stage: str, title: str, request: str, project: str, parent: str,
                 size: str = "large") -> tuple[list[str], str]:
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
            "--priority", str(PRIORITY[size]), "--max-runtime", MAX_RUNTIME.get(stage, MAX_RUNTIME["large"]),
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
    if args.get("fix_of"):
        return fix(args)
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
    # each stage waits on the previous card; content is one card; verify unless verify=false
    chain = {"large": {"map": task_id}, "small": {"build": task_id}, "content": {"build": task_id}}[size]
    stages = {"large": ("plan", "build"), "small": (), "content": ()}[size]
    if args.get("verify", True) and size != "content":
        stages += ("verify",)
    for stage in stages:
        argv, _ = chained_card(stage, title, request, project, task_id, size)
        task_id, error = _create(_stamp(argv, conventions))
        if error:
            return json.dumps({**json.loads(error), "created_so_far": chain})
        chain[stage] = task_id
        if stage == "verify" and size == "small":
            _set_effort(task_id, "low")
    result.update(task_id=chain["build"], cards=chain)
    result.update(
        chat_subscribed=all([_subscribe_calling_chat(tid) for tid in chain.values()]),
        next=("implementer edits, reviewer checks the wording on the same card" if size == "content" else
              ("" if size == "small" else "cheap map -> frontier plan -> ")
              + "implementer plans, builds, tests, and opens a draft PR; reviewer checks the same card and "
              "marks the PR ready" + (" -> verifier runs the tests" if "verify" in chain else ""))
             + "; you get a message on review, block, or completion; the user tests the PR, then merges")
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
    request = _request_of(task["body"])
    project = task["project_id"] or ""
    fix_title = f"Fix {rounds + 1}: {base_title}"
    fix_argv = ["kanban", "create", fix_title, "--assignee", _role("implementer"),
                "--body", FIX_BRIEF.format(round=rounds + 1, source="the verifier ran the reviewed change and it failed",
                                           reporter="from the verifier", request=request, failures=failures,
                                           reviewer=_role("reviewer"), **_branch_state(worktree)),
                *(["--project", project] if project else []), "--workspace", f"dir:{worktree}",
                "--parent", verify_id, "--priority", str(PRIORITY["small"]),
                "--max-runtime", MAX_RUNTIME["build"], "--created-by", "delivery_submit", "--json"]
    fix_id, error = _create(fix_argv)
    if error:
        return error
    argv, _ = chained_card("verify", fix_title, request, project, fix_id)
    reverify_id, error = _create(argv)
    if error:
        return json.dumps({**json.loads(error), "created_so_far": {"fix": fix_id}})
    _set_effort(reverify_id, "low")
    for tid in (fix_id, reverify_id):
        _copy_subscriptions(verify_id, tid)
    return json.dumps({"ok": True, "fix": fix_id, "verify": reverify_id, "round": rounds + 1,
                       "next": "complete your verify card with the failure evidence"})


def fix(args: dict) -> str:
    """The user tested a card's PR and found a problem: one fix card in that card's worktree, same
    branch, same-card review, then a verify card, so the open PR updates. No plan."""
    ref, failures = str(args.get("fix_of") or "").strip(), (args.get("request") or "").strip()
    if not failures:
        return json.dumps({"ok": False, "error": "request is required: what the user found while testing"})
    source_id = ref if re.fullmatch(r"t_[0-9a-f]+", ref) else _card_for_pr(ref)
    task = _task_row(source_id) if source_id else None
    worktree = _card_worktree(source_id) if task else None
    if not worktree:
        return json.dumps({"ok": False, "error": f"no card with a live worktree found for {ref}; "
                           "submit it as new work (size=small) naming the PR branch"})
    request = _request_of(task["body"])
    rounds = len(re.findall(r"^Fix \d+:", task["title"] or ""))
    base_title = re.sub(r"^(Fix \d+: )+", "", task["title"] or "")
    waiting = _waiting_fix(base_title, task["project_id"])
    if waiting:  # one round takes every correction sent before it starts: no second fix + verify chain
        added = _hermes("kanban", "comment", waiting, f"ADDED REQUEST from the user's testing (handle it in this round):\n{failures}")
        if added.returncode == 0:
            _journal("fix.merged", waiting, source=source_id)
            return json.dumps({"ok": True, "task_id": waiting, "merged_into": waiting, "fix_of": source_id,
                               "next": "added to the fix card that has not started yet; it handles both in one round"})
    argv = ["kanban", "create", f"Fix {rounds + 1}: {base_title}", "--assignee", _role("implementer"),
            "--body", FIX_BRIEF.format(round=rounds + 1, source="the user tested the PR and found a problem",
                                       reporter="from the user's testing", request=request,
                                       failures=failures, reviewer=_role("reviewer"), **_branch_state(worktree)),
            *(["--project", task["project_id"]] if task["project_id"] else []),
            "--workspace", f"dir:{worktree}", "--priority", str(PRIORITY["small"]),
            "--max-runtime", MAX_RUNTIME["build"], "--created-by", "delivery_submit", "--json"]
    fix_id, error = _create(_stamp(argv, _conventions(task["project_id"] or "")))
    if error:
        return error
    _copy_subscriptions(source_id, fix_id)
    verify_id = None
    if args.get("verify"):  # the user is the tester of their own feedback; a verifier only when asked
        verify_argv, _ = chained_card("verify", f"Fix {rounds + 1}: {base_title}", request,
                                      task["project_id"] or "", fix_id)
        verify_id, _error = _create(verify_argv)
        _set_effort(verify_id, "low")
    subscribed = all([_subscribe_calling_chat(t) for t in (fix_id, verify_id) if t])
    return json.dumps({"ok": True, "task_id": fix_id, "verify": verify_id, "fix_of": source_id,
                       "worktree": worktree, "chat_subscribed": subscribed,
                       "next": "implementer fixes on the same branch and pushes (the PR updates); reviewer "
                               "re-checks the new change" + (", then the verifier runs the tests" if verify_id else "")
                               + "; you get a message when it is ready to test again. Send further corrections "
                               "with fix_of while it waits: they join this round"})


def _waiting_fix(base_title: str, project) -> str | None:
    """A fix card for the same change that no worker has started yet."""
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        rows = conn.execute("SELECT id, title FROM tasks WHERE status IN ('todo','ready') AND created_by = "
                            "'delivery_submit' AND title LIKE 'Fix %' AND coalesce(project_id,'') = ? "
                            "ORDER BY created_at", (project or "",)).fetchall()
        conn.close()
    except sqlite3.Error:
        return None
    return next((tid for tid, title in rows if re.sub(r"^(Fix \d+: )+", "", title or "") == base_title), None)


def _branch_state(worktree: str) -> dict:
    """FIX BASE sha and the files the branch already changes, so a fix round starts where the last one ended."""
    def git(*a):
        r = subprocess.run(["git", "-C", worktree, *a], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    head = git("rev-parse", "--short", "HEAD") or "unknown"
    try:
        pr_base = subprocess.run(["gh", "pr", "view", "--json", "baseRefName", "-q", ".baseRefName"], cwd=worktree,
                                 capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pr_base = ""
    refs = ([f"origin/{pr_base}"] if pr_base else []) + ["origin/HEAD"]
    base = next((b for ref in refs if (b := git("merge-base", "HEAD", ref))), "")
    stat = git("diff", "--stat=120", f"{base}..HEAD") if base else ""
    return {"fix_base": head, "changed": "\n".join(stat.splitlines()[-40:]) or "(unknown: run git diff --stat against the base)"}


def _set_effort(task_id, effort: str) -> None:
    """Checks that only run commands (verify) do not need the profile's full reasoning effort."""
    if not task_id:
        return
    try:
        from hermes_cli import kanban_db
        from hermes_cli.kanban_db_connect import connect_closing
        with connect_closing() as conn:
            kanban_db.set_reasoning_effort(conn, task_id, effort)
    except Exception:
        pass


def _request_of(body) -> str:
    """The user's REQUEST out of a card body this module wrote."""
    return re.split(r"\n\n(?:CONTEXT:|FAILURES \(|FIND CODE:|Work in)", (body or "").split("REQUEST (from the user):\n", 1)[-1], maxsplit=1)[0]


def _card_worktree(task_id: str):
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        row = conn.execute("SELECT workspace_path FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    path = row[0] if row else None
    return path if path and (Path(path) / ".git").exists() else None


def _card_for_pr(url: str):
    """Newest card whose body or comments mention the PR URL (the implementer's summary carries it)."""
    if not URL_RE.fullmatch(url):
        return None
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT t.id FROM tasks t WHERE t.workspace_path IS NOT NULL AND (instr(t.body, ?) "
            "OR instr(coalesce(t.result, ''), ?) "
            "OR EXISTS (SELECT 1 FROM task_runs r WHERE r.task_id = t.id AND instr(coalesce(r.summary, ''), ?)) "
            "OR EXISTS (SELECT 1 FROM task_comments c WHERE c.task_id = t.id AND instr(c.body, ?))) "
            "ORDER BY t.created_at DESC LIMIT 1", (url,) * 4).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


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


def _worktree_users(worktree: str) -> list[str]:
    """Live cards working in `worktree`: on it directly, or a verify card whose parent built there."""
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        rows = conn.execute(
            f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(LIVE))}) AND (workspace_path = ? "
            "OR id IN (SELECT l.child_id FROM task_links l JOIN tasks p ON p.id = l.parent_id "
            "WHERE p.workspace_path = ?)) ORDER BY created_at", (*LIVE, worktree, worktree)).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    return [r[0] for r in rows]


def _wait_for_worktree(argv: list[str]) -> list[str]:
    """One worktree, one running card: a card for a `dir:` worktree another live card uses waits for it
    (as a parent) instead of editing the same files at the same time."""
    if "--workspace" not in argv or not argv[argv.index("--workspace") + 1].startswith("dir:"):
        return argv
    worktree = argv[argv.index("--workspace") + 1][4:]
    parents = {argv[i + 1] for i, a in enumerate(argv) if a == "--parent"}
    return argv + [x for tid in _worktree_users(worktree) if tid not in parents for x in ("--parent", tid)]


def _create(argv: list[str]) -> tuple[str | None, str | None]:
    """(task id, None) or (None, error JSON) for one `hermes kanban create`."""
    created = _hermes(*_wait_for_worktree(argv))
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
            "Hand a coding task in one of the user's repositories to the cpipe team, "
            "instead of doing it in this chat. size=content when only text, copy, markup, styles, docs, "
            "or a config value change (add/edit a page section, legal wording, labels): one implementer "
            "session plus a wording review, no plan, no tests. size=small for any feature or fix one "
            "developer would do in one sitting, even across several files (the default): the implementer "
            "plans inline, builds, tests, and opens a draft PR, an independent review of the "
            "same card follows, then a verifier runs the tests and marks the PR ready. size=large only for work spanning several repos or modules with "
            "separate owners, or needing a design decision first: a cheap mapper reads the repo, then a "
            "frontier planner plans from that map. Never split one feature into phase cards. "
            "fix_of=<card id or PR URL> when the user tested a PR and found a problem: the request says "
            "what is wrong; one fix card on the same branch updates the PR (reviewed, no verify card unless "
            "verify=true). Corrections sent before that fix card starts join it, so pass each one as it comes. "
            "Returns the card id; progress and the result come back to this chat."),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short imperative card title"},
                "request": {"type": "string", "description": "A brief, not the conversation: the goal, acceptance criteria, and any file, route, or area the user named. Workers see only this. Max 3000 chars. If an acceptance criterion needs a guess about product behavior (which screen, which users, error/empty cases, what stays unchanged), ask the user with clarify first."},
                "project": {"type": "string", "description": "Hermes project slug (see `hermes project list`), e.g. my-app"},
                "size": {"type": "string", "enum": ["content", "small", "large"]},
                "force": {"type": "boolean", "description": "Only when the user confirmed this is new work although an open card looks the same"},
                "verify": {"type": "boolean", "description": "Default true for new small/large work: a verify card runs the tests after review; false only when the user asks to skip it. With fix_of the default is false (the user re-tests); true only when the user asks for a verifier"},
                "fix_of": {"type": "string", "description": "Card id or PR URL the user tested and found broken; request = what is wrong"},
            },
            "required": ["request"],
        },
    },
}
