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
card, at most MAX_FIX_ROUNDS times. cpipe.checks starts the verify beside review, in its own checkout;
a failure then joins the open review round instead of opening a fix card.
fix_of=<card or PR>: the operator found a problem while testing; one fix card in that card's
worktree and branch, reviewed on the same card and then verified, so the open PR updates.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from .home import root as _root_home

HERMES_HOME = _root_home()
# User-submitted work outranks retries of older cards (dispatcher: ORDER BY priority DESC).
PRIORITY = {"content": 30, "small": 20, "large": 10}
# a whole feature is one session: a timeout kills it mid-build and the retry starts cold
LIVE = ("todo", "ready", "running", "review", "scheduled")  # blocked/triage wait on the user: not live
MAX_FIX_ROUNDS = 2
MAX_REQUEST_CHARS = 3000
DUPLICATE_TITLE_RATIO = 0.85
STAGE_PREFIX = re.compile(r"^(map|plan|verify|fix \d+|review)\s*:\s*", re.I)
REVIEW_TITLE = re.compile(r"^\s*(code[\s-]*)?review\b|^\s*re-?review\b", re.I)
URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/(?:pull|issues)/\d+")

CONTEXT = """CONTEXT: this card is your whole assignment: the REQUEST, the code it touches and its
callers, and the parent card's result (kanban_show -> parents). No broad searches, no web, no
skills beyond your role's, no other cards or conversations. Make independent reads, searches, and
commands in ONE turn (several tool calls at once): every extra turn is a full model round trip.
"""

# Planner designs by them, implementer follows them, reviewer cites them by name.
PRINCIPLES = """PRINCIPLES (planner designs by these, implementer follows them, reviewer cites one by name when the
diff breaks it; anything else is a note, not a finding):
- REUSE: an existing helper, pattern, or component wins over new code; the plan names it. Then the
  standard library, a platform feature, or an installed dependency; never a new dependency for a few lines.
- SMALLEST CHANGE that meets the REQUEST: no speculative abstraction, config, dependency, or refactor.
  Smallest COMPLETE change: what a careful senior ships with the feature unasked (the plan's
  COMPLETE FEATURE lines) is part of the REQUEST, not an extra.
- KNOWN SHORTCUTS: a deliberate simplification with a real ceiling (naive scan, global lock) gets a
  one-line comment naming the ceiling and the upgrade path.
- ROOT CAUSE: fix it once where every caller routes through, not in each caller.
- KEEP BEHAVIOR: existing behavior stays unless the REQUEST changes it; pinned by tests. Before
  removing or loosening a filter, limit, or check, find why it exists (`git log -S`, blame); no
  clear reason -> ask (OPEN DECISION).
- TRUST BOUNDARIES: validate input where it enters; never log secrets, tokens, or personal/health data.
- ERRORS: handle them where something can act; never swallow one silently.
- MATCH THE CODE AROUND IT: its naming and idiom; the linter and formatter own formatting.
- TESTS PROVE BEHAVIOR through real code (never source text); one behavior per commit.
"""

SCOPE = CONTEXT + """
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

""" + PRINCIPLES

# One plan shape for the planner card and the implementer's own plan; review_gate checks the headings.
PLAN_FORMAT = """PLAN FORMAT: every heading below, in this order, at the start of a line. Write `none` under a
heading that does not apply; never drop it. Every file reference is path:line from the map or a file
you opened. One line per item.
SHAPE: ui | api | data | job | integration | bug | mixed. It sets where the detail goes: ui = ENTRY
  POINTS (every route and in-app link) and mounted tests; api/data = CONTRACTS field by field and any
  migration; job = trigger, schedule/timezone, safe to run twice; integration = the external contract
  and where tests stub it; bug = REPRO first. Other headings stay short or `none`.
GOAL: one sentence.
ACCEPTANCE: AC1..n, each observable, each ending `-> TEST <file> "<test name>"` or `-> MANUAL <step>`.
  This list is the test contract: the implementer writes exactly these, the reviewer checks them.
NON-GOALS: only what the REQUEST excludes, quoted.
ENTRY POINTS: each way users or systems reach the change (direct URL, in-app navigation/router,
  API client, job, another package) -> path:line -> IN SCOPE -> ACn, or NON-GOAL with the REQUEST's words.
  Never drop one by assumption: check how users actually get there.
CONTRACTS: each value crossing a boundary: sender path:line field -> receiver path:line field ->
  agree | MISMATCH (fixed in slice N). Client payload, route, model/DB, response.
REUSES: existing helpers, patterns, or components the change calls or copies, path:line.
ADD: each new file, function, route, component: purpose, slice N. (NEW: tests after the code.)
CHANGE: each existing symbol path:line: what changes, slice N. (MODIFIED: pinned first.)
DON'T TOUCH: files that look related but must not change, and why.
PINS: each caller behavior a CHANGE reaches -> covered by <test> | UNPINNED -> <test to add>.
EDGE CASES: for this change, each that applies -> ACn/test, or `none`: invalid or empty input, missing
  or revoked access, concurrent or repeated calls, other paths to the same state (tabs, links, jobs),
  time and timezone, large or long data.
COMPLETE FEATURE: what a careful senior ships with this unasked; each that applies -> ACn/test, or
  `none`: the user never guesses (every disabled control, rejected input, or failure says why and what
  to do); choices that cannot combine cannot be picked together; the same thing looks and works the
  same everywhere (sibling controls on the screen, every page that shows it, the existing email/SMS/
  push formats); records and users created before the change (default, backfill, re-verify);
  production volume (queries and loops at real data size: index, batch, bound); side effects (SMS,
  email, push, webhook) that fail without breaking or rolling back the main flow; writes and deletes
  only what the REQUEST names; deploy needs (new env vars in the example file with placeholders,
  migration, flag).
TESTS: each test file -> copies harness <existing test that already mounts this component or calls
  this route> -> enters at <the real entry: route, mounted view, event, job> -> asserts at <the real
  receiving side: response, stored record, rendered DOM, emitted event> -> command. Mock only external
  edges (network, provider, clock), never the module under change. AVOID: reading or regex-matching
  source, a copied router or handler, calling a helper the entry point does not go through. No harness
  in the repo: say so and add the smallest real one as a slice.
CHECK: each command to run after the build (related tests in every package, lint, typecheck,
  build) and the result that counts as pass.
RISKS: what could regress, where, and which test or CHECK catches it.
OPEN DECISIONS: product questions the code cannot answer, with 2-4 options, recommended first; or none.
VERIFY: facts the map left open that the implementer confirms first (path or command), or none.
SLICES: ordered, one line each: `S<n>: files | test command | commit "<Conventional Commit>"`.
  The implementer commits once per slice, so a retried run resumes at the first missing commit.
"""
PLAN_TASK = """Produce the implementation plan in the PLAN FORMAT below. The first slice adds every UNPINNED
test, passing on today's code. If an OPEN DECISION changes what gets built, block this card with it
instead of guessing. PINS cover behavior only: static text, markup, and styles need none. Plan the
smallest complete change: nothing unrelated (no refactors, speculative hardening, or tests the plan
does not need); the COMPLETE FEATURE lines are part of the request, not extras.
""" + PRINCIPLES + """
Complete this card with the FULL plan in `result` (not only the summary): the
implementer card waiting on this one reads it from there and has no other copy.

""" + PLAN_FORMAT
PLAN_HEADINGS = re.findall(r"^([A-Z][A-Z' -]+):", PLAN_FORMAT.split("\n", 2)[2], re.M)

MAP_BRIEF = """REPOSITORY MAP for a planner. Read-only: no edits, no commits, no plan.
token_budget: low.

REQUEST (from the user):
{request}

""" + SCOPE + """
Read only the code this request touches, then complete this card with the map (SHAPE (ui, api, data,
job, integration, bug, or mixed), FILES, SYMBOLS, REUSE (existing helpers/patterns that already do
part of this), EXCERPTS (the exact current lines, at most 15 each, of every place likely to change),
CALLERS, ENTRY POINTS (every way a user or system reaches this behavior or content: direct URL,
in-app router/navigation, API clients, jobs, other packages that render or call it), CONTRACTS
(field names and shapes at each handoff: client payload -> route -> model/DB -> response),
TESTS (plus HARNESS: an existing test that already enters through this route, view, event, or job and
asserts on the real result, and its command), UNPINNED (caller behaviors this change reaches that no test covers),
SIBLINGS (the nearest existing feature of the same kind and its format: neighbouring controls, message
templates, settings rows), SCALE (tables/collections touched, their indexes), DEPLOY (env example
file, migrations, flags), COMMANDS, CONVENTIONS, GAPS; about 8 KB, every path:line from a file you opened).
The planner card waiting on this one does not open files: it plans only from your map, so anything
missing here becomes a guess. Be complete on ENTRY POINTS, CONTRACTS, and HARNESS.
"""

PLAN_LOOKUPS = 6  # card_gate refuses the planner more file reads/searches/terminal calls on a Plan: card
PLAN_BRIEF = """LARGE TASK: plan from the repository map. Do not load skills.
token_budget: medium.

REQUEST (from the user):
{request}

""" + CONTEXT + """
PLAN, DON'T EXPLORE. The parent card's result is a repository map made by a cheaper model: it is your
evidence. Do not explore the repo, run tests, or install anything. At most {lookups} lookups (file
reads, searches, terminal), each to close one GAP you name; the plugin refuses more. A fact still
open goes under VERIFY for the implementer.

""" + PLAN_TASK

IMPLEMENTER = """RETRY: if an earlier run of this card stopped (timeout, rate limit, crash), run `git log --oneline
<base>..HEAD` and resume at the first plan slice not yet committed; never redo committed work.

IMPLEMENTER
1. PLAN. If a parent card's result is a plan, follow it; do not re-plan; confirm its VERIFY items first. Otherwise plan in one
   pass yourself: read the code the REQUEST touches and its direct callers (for a route, grep its
   URL path in server AND client; for content or a page, every place it renders and every way users
   navigate to it), then post the plan as a card comment in the PLAN FORMAT at the end of this
   card, one line per item (a two-file change fits in about 1.5 KB). NEW = a new file, function,
   route, or component nothing existing calls yet; MODIFIED = existing code whose behavior callers
   rely on. Add any reachable caller a parent plan missed to PINS. Plan nothing unrelated to the
   REQUEST; COMPLETE FEATURE items belong to it.
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
   Commit on this card's branch and push it (`git push -u origin HEAD`). Never create, switch, or rename
   the branch: the coordinator named it. On the first push open a
   draft PR against the PR base (the card's `PR BASE:` line, else the project's base branch; `gh pr create --draft --base <it>`). The title and body describe
   the whole branch (`git diff <base>...HEAD`), never only the latest commit: after every later
   push, rewrite them with `gh pr edit <url> --title ... --body ...` to cover all the changes so
   far. Write them with the `creating-pr-content` skill when it is installed (load it once per
   card) and run its linter before each create/edit. The PR is public: never name the user,
   bots, tools, card ids, local paths, or emails. It describes the change as it stands now, never
   its history: no review rounds, corrections, findings, SHAs, or delivery status; Verification lists
   commands a reader can run and their results. The body includes `## How to test`: 3-6 manual steps a person follows to see the whole
   change work, and what could regress. If push or the PR fails, record the exact error as
   READY_WITH_RISK.
5. If a check cannot run because the environment lacks a tool, dependency, config, or secret,
   record the exact gap in your summary and continue (READY_WITH_RISK). Do not block for it.
   The reviewer's own gates belong to the reviewer: do not run them and never block on them.
6. Hand this SAME card to review: `hermes kanban request-review <this card id> --reviewer {reviewer}
   --summary "<PR url; changed files; OUT_OF_PLAN files; PINNED: <test ids>; commands run and
   results; commit sha; any READY_WITH_RISK gaps>"`.
7. If review requests changes: check each finding, and each VERIFY FAILED comment on this card, against the code first. Fix the right ones on
   this card, one at a time, rerunning the test that showed each. A finding that is wrong for this
   code gets a reply with the evidence (the test, the line, the constraint) instead of a change made
   only to close it; never fix something adjacent and call the finding done. Push and request
   review again, listing each finding as fixed (commit) or disputed (reason).

REVIEWER (same worktree)
- Review `git diff <base>...HEAD` in this card's worktree against the REQUEST and the plan (the
  parent card's result, or the implementer's plan comment). One pass: list every finding at once
  with file:line. Walk the plan: every ACCEPTANCE item met, every IN SCOPE ENTRY POINT works, every
  CONTRACTS mismatch fixed, every EDGE CASES and COMPLETE FEATURE line handled, every TESTS line
  exists and runs the real entry to the real receiving side, and no changed file
  outside ADD/CHANGE without an OUT_OF_PLAN note. A plan item skipped silently is a missed requirement.
  Each ACCEPTANCE `-> TEST` must exist and pass. A test the plan did not name is a request only
  when a behavior is broken or a reachable caller is unpinned; otherwise note it as residual risk.
  A PRINCIPLES finding names the principle and the diff line; style and preference are notes.
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
  component or a real route/function call asserting the effect: REQUEST_CHANGES. Ask "if this
  behavior broke, would an assertion fail?": snapshot-only, no-throw, or only-asserts-a-mock-was-
  called tests, and tests that mock the module under change, do not count.
  Mocking a dependency at a boundary (HTTP client, database, provider, router) is fine; never
  ask for a deeper harness (real router, database, rendered file, browser) the REQUEST or plan
  does not name: note it as residual risk.
- A DIAGNOSIS comment (bug card): run its REPRO at BASE (must fail for the reported reason) and at
  HEAD (must pass), and check the change sits at the ROOT CAUSE instead of masking the symptom.
  Any removed or loosened existing condition, filter, or check (bug or not) needs its history in
  the DIAGNOSIS or plan; without it, REQUEST_CHANGES.
- Behavior the REQUEST did not ask for (new handlers, refactors, changed defaults, "while I was
  here" hardening) is a finding: revert it, or list it so the user approves it. COMPLETE FEATURE
  items are not unrequested.
- Use it as the user would: a control disabled with no reason, an input rejected silently, a side
  effect (SMS, email, push) whose failure breaks the main flow, a sibling on the same screen that
  now looks or works differently, or a query unbounded at production size is a missed
  requirement, even when the plan said `none`.
- REQUEST_CHANGES only for correctness, security, data-loss, regression, a missed requirement, a
  missing pin or behavior test, or unrequested behavior. Style and naming go in the approval as notes. Missing tools are noted, not blockers.
  Anything the REQUEST needs is in scope even when a plan did not name the file; never ask for a
  test, harness, or file the REQUEST excludes: note it as a follow-up.
- On a `Fix N:` card, review only `git diff <FIX BASE from the card>..HEAD` and the callers of what
  changed there: the earlier rounds were already approved.
- On re-review, report only on the delta since the last reviewed commit plus the prior findings
  (read outside it: a break the delta causes in another file is a delta finding). From round two:
  no new Medium/Low findings; a new High on code an earlier round already reviewed says why it
  was not raised then (a fix revealed it, a file arrived, an assumption proved wrong, or you did
  not look) or it is a note. Withdraw your own earlier findings that were scope creep or
  preference, and say so. A disputed finding is judged on the implementer's evidence, not
  restated.
  An unchanged patch (a rebase or empty push: `git diff $(git merge-base origin/<base> <sha>) <sha> |
  git patch-id --stable` equal for the last reviewed sha and HEAD) keeps the prior verdict: re-run
  the related tests only.
  A prior Medium/Low finding the implementer could not or would not fix is not a second
  REQUEST_CHANGES: approve with it under RESIDUAL RISK. At most 3 review rounds: on the third,
  approve with Medium/Low items under RESIDUAL RISK. RESIDUAL RISK never holds a High (wrong
  behavior, security, data loss, regression): if one is still open, block this card with the
  finding and 2-3 options so the user decides; never approve over it.
- A VERIFY FAILED comment on this card is a High finding: REQUEST_CHANGES with it, unless its failing
  command passes at HEAD.
- On APPROVED: if kanban_show lists a `Verify:` child card or a PARALLEL CHECK comment, leave the PR
  draft (cpipe marks it ready once verify passes on the approved commit); otherwise mark it ready
  (`gh pr ready <url>`); a failure is a note.
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
  renders, and files outside the request. No tests, pins, harnesses, review gates, or caller traces:
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

WHERE: a PARALLEL CHECK comment means your workspace is a detached checkout of the commit handed to
review: work there while the reviewer reviews, and take the PR url and PINNED tests from that comment.
Otherwise work in the parent card's worktree (kanban_show -> parents -> workspace; the workspace note
tells you when you start elsewhere). BASE = `git merge-base HEAD <base branch from the review handoff,
else origin's default branch>`.
1. PROOF: `git diff --name-only BASE..HEAD`. Restore the changed NON-test files to BASE
   (`git checkout BASE -- <files>`), run the changed/new tests: at least one requested-behavior
   test must FAIL, and every test the review handoff lists as PINNED must PASS (a pin failing at
   BASE was written for the new code: FAIL "pin does not assert current behavior"). Then
   `git checkout HEAD -- <files>` and confirm `git status --porcelain` is empty.
   No changed tests for a behavior change is a FAIL ("no test proves the change").
2. RELATED: run the tests related to every changed file in each package the change reaches
   (`npx jest --findRelatedTests`, `npx vitest related --run`, or the importing tests; a related-test
   runner your first-turn notes name does this and PRE-EXISTING in one command). All pass.
   PRE-EXISTING: a failing suite, unhandled error, or hang is not a FAIL when it also fails with the
   changed files restored to BASE (re-run just that file at BASE, then restore HEAD). List it under
   PRE-EXISTING with both results; the user owns it. Only failures new at HEAD count.
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
since that sha (`git diff --name-only <sha>..HEAD`); skip any failure the earlier verify listed as
PRE-EXISTING.
NO BROWSER OR LIVE QA unless the REQUEST asks for it: no dev servers, logins, or browser tools. The
user tests the PR by hand. LIVE QA the REQUEST asks for but you cannot run here (a real device, a browser automation permission, a server, account, or
secret you lack) is not a failure: list it under NOT VERIFIED for the user to check by hand and judge
on what you did run, never a reason to block.
RESULT: the first line is `VERDICT: PASS <sha>` or `VERDICT: FAIL <sha>` (`git rev-parse HEAD`, the commit
you verified), then each command and its result. Never mark the PR ready: cpipe does once review has
approved the same commit.
PASS or FAIL: complete this card. On FAIL list each failure exactly (command, test, error): cpipe
sends them to the open review round or opens the fix round. If completing says the round limit is
reached, block this card with the failures instead.
"""

LIVE_QA_BRIEF = """LIVE QA (the user asked for it; it overrides NO BROWSER above). Run it after step 3, with the
worktree back at HEAD:
1. Start the app: `delivery_test_env` with target {build}. If it fails, list LIVE QA under NOT VERIFIED with
   its error and judge on the rest; never start servers by hand, never block for it.
2. Login: {login}
3. Open only the screens the change touches (the diff and the review handoff say which). For each one:
   a screenshot at 1440px and at 390px wide checked with vision against the REQUEST and the states it
   names (loading, empty, error, disabled, long text), and `browser_console` for errors and failed requests.
4. FAIL, as for a test: a broken flow, an acceptance criterion that does not work, or a console error or
   failed request this change introduced. Design issues (spacing, alignment, hierarchy, contrast, copy)
   are never a FAIL: list each under DESIGN NOTES with the screen and the fix; the user decides.
5. Save the screenshots in {shots}/ and list their paths. Never attach them to the PR or post them
   anywhere: logged-in screens show real data.
END LIVE QA
"""
QA_LOGIN_FILE = ("read the test account from `{path}` (JSON: username, password, notes). Type it only into the "
                 "app's login form; never put the password in a comment, result, PR, commit, or file.")
QA_NO_LOGIN = "the user gave no test account: test what works without one and list the rest under NOT VERIFIED."
LIVE_QA_RE = re.compile(r"LIVE QA \(the user asked for it.*?END LIVE QA\n", re.S)


DESIGN_BRIEF = """DESIGN {letter} of 3: mock up how this could look. Do not build it. Three designers work at the
same time, each in a different direction, and the user picks one before any code is written. Never edit, commit,
install or push in the repository; no PR.

REQUEST (from the user):
{request}

DIRECTION {letter}: {direction}
REFERENCES from the user: {refs}

CODE (read-only): {code}
Read the screens this request touches and the styles they use (theme tokens, component library, fonts, spacing,
icons) so the mockup looks like this app, unless your direction says otherwise.

DELIVER in your workspace (the current directory), loading the `frontend-design` skill if you have it:
1. `design-{letter}.html`: one self-contained file (inline CSS and JS, no build step; web fonts are the only external
   assets) that opens in a browser. Desktop (1440px) and mobile (390px) layouts behind a toggle, realistic data, and
   the loading, empty and error states the screen has. Interactions the request names work in plain JS.
2. With a browser tool: `design-{letter}-desktop.png` and `design-{letter}-mobile.png` screenshots of it.
RESULT: first line `DESIGN {letter}: <the idea in one line>`, then 3-5 bullets on what sets this direction apart and
what it costs to build (new components, data the app does not have yet). Name each file by its absolute path: that
keeps it after the card ends and sends it to the user.
DESIGN SET: {key}
"""
DESIGN_DIRECTIONS = {
    "A": "closest to the app as it is: its existing components, tokens and patterns; the smallest visual change "
         "that fully meets the request.",
    "B": "follow the user's REFERENCES as closely as this app allows. With none, follow the best-known product that "
         "solves this problem well, and name it.",
    "C": "a fresh take: a layout or interaction A (the app as it is) and B (the references) would not choose, still "
         "usable by the app's current users.",
}
DESIGN_SET_RE = re.compile(r"^DESIGN SET: (d_[0-9a-f]+)$", re.M)
PICKED_DESIGN = """PICKED DESIGN {letter} (the user chose it from three mockups before this card was made): build the
REQUEST to look and behave like it. The mockup is the target for layout, hierarchy, spacing, copy and states; build it
with the app's real components, tokens and data, not the mockup's code. Open the file in a browser to see it.
{files}
The designer's summary:
{summary}
USER NOTES on the pick (they override the mockup): {notes}
END PICKED DESIGN
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
REPRODUCE each failure on FIX BASE first (a test or command that goes RED for the reported reason)
and trace it to its cause; before removing an existing condition, read its history (`git log -S`).
One that does not reproduce: list it in the review handoff with what you ran, fix the rest. One
that needs a product decision: block with the question (2-4 options, recommended first).
New requests the user adds while this card waits arrive as comments: handle each in this round.
A request that needs no code change (run something locally, reproduce, set up data for the user to
test): do only that, and the moment it works complete this card with `hermes kanban complete <this
card id> --summary "<URLs / how to use it / what you verified>"` with no new commit, so the user gets it
now. No extra hardening, receipts, or review for it.
Fix these failures only, then follow steps 3-7 (PINNED tests must still pass):
""" + SCOPE + "\n" + IMPLEMENTER


BUG_DIAGNOSE = """BUG REPORT: DIAGNOSE BEFORE YOU FIX. This replaces IMPLEMENTER step 1's planning, even with a
parent plan: a cause is a guess until it reproduces. Load `systematic-debugging` once if installed.
a. REPRODUCE the user's exact symptom on the unchanged code: a focused test through the real route,
   job, or component, a script, or a read-only query on a local/test database (never write to a
   shared one). Run it: it must go RED for the reported reason, not merely error.
b. TRACE from the symptom back to its cause (the data, query, condition, or time/locale boundary
   that produces it), confirming each step by running or printing it, not by reading alone.
c. INTENT: before you change or remove an existing condition, filter, or check, read its history
   (`git log -S'<code>' --oneline`, `git blame -L`, that commit's message). If it is there on
   purpose, fix the cause and keep the purpose.
d. Post ONE card comment: DIAGNOSIS (SYMPTOM: the report, quoted. REPRO: command -> the RED lines.
   ROOT CAUSE: path:line and why it produces the symptom. INTENT: what the history says, or `new
   code`. FIX: the smallest change at the root cause and every caller it reaches.), then the plan
   in the PLAN FORMAT with ACCEPTANCE including "REPRO goes GREEN".
e. It does not reproduce, or two causes fit and only the user can say which behavior is right:
   block with the DIAGNOSIS so far and the exact question (2-4 options, recommended first). Never
   ship a guessed fix. The REPRO is step 3's requested-behavior test: RED now, GREEN after.
"""

BUG_MAP = """BUG REPORT: also map SUSPECTS (each path:line that could produce the symptom, with the evidence
for and against), HISTORY (`git log -S` for the conditions involved: why they exist), and REPRO
(the test, script, or read-only query that should show the symptom, and its command). Do not decide
the cause and do not fix anything.
"""

BUG_PLAN = """BUG REPORT: plan the proof, not a guessed fix. The first slice is the REPRO test (RED on today's
code for the reported reason). GOAL names the root cause only when the map proves it; otherwise
"confirm the cause among <suspects>" and the fix slice says which suspect each result points to.
Never plan removing an existing condition without its HISTORY.
"""


@dataclass(frozen=True)
class Step:
    """One card of a flow. `key` goes on the card as current_step_key: the tick, the gates and fix/verify
    lookups match on it, never on the title. `brief` is formatted with the flow's context."""
    key: str
    role: str
    brief: str
    runtime: str
    prefix: str = ""  # title prefix, formatted with the context ("Fix {round}: ")
    workspace: str = "worktree"  # worktree (in the project) | scratch (no project) | dir (the PR worktree)
    parallel: bool = False  # starts beside the previous step (same parent) instead of after it
    variant: str = ""  # design letter
    fallback: str = ""  # role used when the roster has none for `role`


BUILD = Step("build", "implementer", BUILD_BRIEF, "60m")
# scratch with no project: Hermes turns a project's scratch card into its own fresh worktree off the base branch,
# where 1 in 30 verifies checked the wrong code. workspace_prep sends it to the build worktree.
VERIFY = Step("verify", "verifier", VERIFY_BRIEF + "BUILD CARD: {parent}\n", "60m", "Verify: ", "scratch")
FIX = Step("fix", "implementer", FIX_BRIEF, "60m", "Fix {round}: ", "dir")
FLOWS = {
    "content": (Step("build", "implementer", CONTENT_BRIEF, "25m"),),  # same-card review, no plan or verify
    "small": (BUILD, VERIFY),  # the implementer plans inline (IMPLEMENTER step 1)
    "large": (Step("map", "investigator", MAP_BRIEF, "20m", "Map: "),  # cheap map, frontier plan
              Step("plan", "planner", PLAN_BRIEF, "20m", "Plan: "), BUILD, VERIFY),
    "fix": (FIX, replace(VERIFY, prefix="Verify: Fix {round}: ")),
    # three directions at once; scratch with no project keeps the files they name (a worktree would not)
    "design": tuple(Step("design", "designer", DESIGN_BRIEF, "25m", f"Design {v}: ", "scratch", True, v, "implementer")
                    for v in DESIGN_DIRECTIONS),
}
# Where a delivery goes after each step's outcome. The tick (checks.outcome) reads a finished card's outcome and
# runs checks.ACTIONS[action]; "next" is Hermes starting the child card; "ask_user" is the verdict gate making
# the verifier block its card; "user_pick" is delivery_design_pick. tests/test_flows.py keeps it free of dead ends.
EDGES = {
    ("map", "done"): "next",
    ("plan", "done"): "next",
    ("build", "in_review"): "start_verify",  # verify runs beside review
    ("build", "done"): "next",
    ("fix", "in_review"): "start_verify",
    ("fix", "done"): "next",
    ("design", "done"): "user_pick",
    ("verify", "wait"): "none",  # passed while review is open: join when it is approved
    ("verify", "fail_review"): "to_review",  # failed while review is open: the failures join that round
    ("verify", "pass"): "ready",  # passed on the approved commit
    ("verify", "pass_old"): "delta",  # review changed the code after it ran: re-verify what changed
    ("verify", "fail_old"): "delta",
    ("verify", "fail"): "fix",  # failed on the approved commit: the fix flow, then verify again
    ("verify", "fail_limit"): "ask_user",  # MAX_FIX_ROUNDS fix rounds already ran
    ("verify", "fixing"): "none",  # a fix round is already open for it
    ("verify", "no_verdict"): "none",
    ("verify", "orphan"): "none",  # its card is gone or has no worktree
}
DELIVERY_RE = re.compile(r"^DELIVERY: (t_[0-9a-f]+)$", re.M)  # the delivery's first card, on every later card
STEP_TITLES = {"verify": "Verify:%", "fix": "Fix %", "plan": "Plan:%", "map": "Map:%", "design": "Design %"}


def step_sql(t: str = "tasks") -> str:
    """SQL for a card's step: its tag, else (cards made before tagging) its title."""
    whens = " ".join(f"WHEN {t}.title LIKE '{like}' THEN '{key}'" for key, like in STEP_TITLES.items())
    return f"coalesce({t}.current_step_key, CASE {whens} ELSE 'build' END)"


def _step_role(step: Step) -> str:
    try:
        return _role(step.role)
    except KeyError:
        if not step.fallback:
            raise
        return _role(step.fallback)


def card_argv(step: Step, title: str, request: str, project: str, parent: str | None = None,
              priority: int = PRIORITY["small"], worktree: str | None = None, **ctx) -> tuple[list[str], str]:
    """(`hermes kanban create` argv, assignee) for one step. Pure, for testing."""
    assignee = _step_role(step)
    ctx = {"round": 1, **ctx}
    body = step.brief.format(request=request.strip(), reviewer=_role("reviewer"), lookups=PLAN_LOOKUPS,
                             parent=parent, letter=step.variant, direction=DESIGN_DIRECTIONS.get(step.variant), **ctx)
    workspace = f"dir:{worktree}" if step.workspace == "dir" else step.workspace
    scope = ["--project", project] if project and step.workspace != "scratch" else []
    return ["kanban", "create", step.prefix.format(**ctx) + title, "--assignee", assignee, "--body", body,
            *scope, "--workspace", workspace, *(["--parent", parent] if parent else []),
            "--priority", str(priority), "--max-runtime", step.runtime,
            "--created-by", "delivery_submit", "--json"], assignee


def run_flow(flow: str, title: str, request: str, project: str, *, parent: str | None = None, skip=(),
             delivery: str | None = None, edit=lambda step, argv, chain: argv, after=lambda step, tid: None,
             **kw) -> tuple[dict, str | None]:
    """Create a flow's cards in order: each waits on the one before (a parallel step on the same parent as
    its siblings), tagged with the flow and step. Every card names its `delivery` (default: the first card).
    (chain {step or variant: id}, error JSON or None)."""
    chain, prev = {}, parent
    for step in FLOWS[flow]:
        if step.key in skip:
            continue
        argv, _ = card_argv(step, title, request, project, parent if step.parallel else prev, **kw)
        argv = _add_body(argv, f"DELIVERY: {delivery}") if delivery else argv
        tid, error = _create(edit(step, argv, chain))
        if error:
            return chain, (json.dumps({**json.loads(error), "created_so_far": chain}) if chain else error)
        chain[step.variant or step.key] = tid
        prev = prev if step.parallel else tid
        delivery = delivery or (None if step.parallel else tid)
        _tag(tid, flow, step.key)
        after(step, tid)
    return chain, None


def delivery_of(task_id: str, body) -> str:
    return (m := DELIVERY_RE.search(body or "")) and m[1] or task_id


def _fix_rounds(delivery: str) -> tuple[int, int]:
    """(fix cards, fix cards a failed verify opened) in a delivery."""
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        try:
            return tuple(conn.execute(
                "SELECT count(*), coalesce(sum(EXISTS (SELECT 1 FROM task_links l JOIN tasks v ON v.id = l.parent_id "
                f"WHERE l.child_id = f.id AND {step_sql('v')} = 'verify')), 0) FROM tasks f "
                f"WHERE {step_sql('f')} = 'fix' AND instr(f.body, ?) > 0", (f"DELIVERY: {delivery}",)).fetchone())
        finally:
            conn.close()
    except sqlite3.Error:
        return 0, 0


def verify_rounds(verify_id: str, title, body) -> tuple[int, int]:
    """(fix rounds, fix rounds a failed verify opened) in a verify card's delivery; a card made before
    deliveries were named counts by its title."""
    if DELIVERY_RE.search(body or ""):
        return _fix_rounds(delivery_of(verify_id, body))
    m = re.match(r"Verify: Fix (\d+): ", title or "")
    return (int(m[1]),) * 2 if m else (0, 0)


def _tag(task_id: str, flow: str, step: str) -> None:
    """`hermes kanban create` cannot set the step columns; Hermes copies current_step_key onto each run."""
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=rw", uri=True, timeout=10)
        try:
            with conn:
                conn.execute("UPDATE tasks SET workflow_template_id = ?, current_step_key = ? WHERE id = ?",
                             (flow, step, task_id))
        finally:
            conn.close()
    except sqlite3.Error:
        pass  # untagged cards still match on their title


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
    """The first card of a submission's flow."""
    return card_argv(FLOWS[size][0], title, request, project, priority=PRIORITY[size])


def chained_card(stage: str, title: str, request: str, project: str, parent: str,
                 size: str = "large") -> tuple[list[str], str]:
    """A 'plan', 'build' or 'verify' card that starts only after `parent` is done."""
    step = next(s for s in FLOWS["large"] if s.key == stage)
    return card_argv(step, title, request, project, parent, PRIORITY[size])


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


def branch_name(project: str, branch: str, exists=None) -> str | None:
    """The coordinator's branch name, or None (Hermes's default). A taken name gets `-2`, `-3`, ...:
    Hermes would otherwise reuse the old branch."""
    branch = (branch or "").strip()
    if not branch:
        return None
    exists = exists or _branch_exists(project)
    n, candidate = 1, branch
    while exists(candidate):
        n += 1
        candidate = f"{branch}-{n}"
    return candidate


def _project_repo(project: str):
    """The project's primary repo path, or None when unknown."""
    try:
        from hermes_cli import projects_db
        with projects_db.connect_closing() as conn:
            proj = projects_db.get_project(conn, project)
        return (proj and (proj.primary_path or (proj.folders[0].path if proj.folders else None))) or None
    except Exception:
        return None


def _branch_exists(project: str):
    """Checker for local or origin branches in the project's primary repo (never true when unknown)."""
    repo = _project_repo(project)
    if not repo:
        return lambda name: False
    return lambda name: any(subprocess.run(["git", "-C", repo, "show-ref", "--verify", "--quiet", ref]).returncode == 0
                            for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"))


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
    if args.get("design") and not args.get("picked_design"):
        return design(args, title, request, _project_repo(project))
    bug = args.get("kind") == "bug" and size != "content"
    live_qa = args.get("live_qa")
    conventions = "\n\n".join(filter(None, [_conventions(project, args.get("base") or ""), args.get("picked_design")]))
    branch = branch_name(project, args.get("branch") or "")

    def edit(step, argv, chain):
        if bug and step.key != "verify":
            argv = _as_bug(argv, step.key)
        if step.key == "verify" and live_qa:
            argv = _add_body(argv, live_qa_section(chain["build"], args.get("qa_login")))
        if step.key == "build" and branch:  # the implementer works on the build card
            argv = [*argv, "--branch", branch]
        return _stamp(argv, conventions)

    def after(step, tid):
        if step.key == "verify" and size == "small" and not live_qa:
            _set_effort(tid, "low")

    try:
        chain, error = run_flow(size, title, request, project, priority=PRIORITY[size], edit=edit, after=after,
                                skip=() if args.get("verify", True) else ("verify",))
    except (OSError, KeyError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    if error:
        return error
    result = {"ok": True, "task_id": chain["build"], "assignee": _step_role(FLOWS[size][0]), "size": size,
              "cards": chain}
    result.update(
        chat_subscribed=all([_subscribe_calling_chat(tid) for tid in chain.values()]),
        next=("implementer edits, reviewer checks the wording on the same card" if size == "content" else
              ("" if size == "small" else "cheap map -> frontier plan -> ")
              + "implementer plans, builds, tests, and opens a draft PR; reviewer checks the same card and "
              "marks the PR ready" + (" -> verifier runs the tests" if "verify" in chain else ""))
             + "; you get a message on review, block, or completion; the user tests the PR, then merges")
    return json.dumps(result)


def _as_bug(argv: list[str], stage: str) -> list[str]:
    """Bug report: the build card diagnoses before it plans; map and plan cards look for the cause."""
    i = argv.index("--body") + 1
    body, step1 = argv[i], "IMPLEMENTER\n1. PLAN."
    if stage == "build" and step1 in body:
        body = body.replace(step1, BUG_DIAGNOSE + "\n" + step1, 1)
    else:
        body += "\n" + {"map": BUG_MAP, "plan": BUG_PLAN}.get(stage, BUG_DIAGNOSE)
    return [*argv[:i], body, *argv[i + 1:]]


def _conventions(project: str, base: str = "") -> str:
    """The project's saved conventions for a card body. `base`: this card's PR base when it is not the
    project's (the user named one, or a fix follows its PR): stamped so the worker hook and the
    reviewer use it instead of the project's base_branch."""
    from . import workspace_prep
    profile = workspace_prep.project_profile(project, HERMES_HOME)
    base = (base or "").strip()
    if not base or base == profile.get("base_branch"):
        text = workspace_prep.profile_brief(profile)
    else:
        text = (f"PR BASE: `{base}` (start from origin/{base} and open the PR against it; this overrides the "
                "project's base branch below)\n" + workspace_prep.profile_brief({**profile, "base_branch": base}))
    return "\n\n".join(filter(None, [text, lessons_brief(project)]))


MAX_LESSONS = 10  # per card: the newest win, so the brief never grows without bound


def lessons_brief(project: str) -> str:
    """The operator's approved lessons for this project (and "*" for every project), from
    $HERMES_HOME/delivery/lessons.yaml: `<project>: [text, or {text: ..., added: ...}]`."""
    try:
        import yaml
        data = yaml.safe_load((HERMES_HOME / "delivery" / "lessons.yaml").read_text()) or {}
    except Exception:
        return ""
    items = [*(data.get("*") or []), *((data.get(project) or []) if project else [])]
    texts = [str(i.get("text") if isinstance(i, dict) else i).strip() for i in items]
    texts = [t for t in texts if t and t != "None"][-MAX_LESSONS:]
    return ("LESSONS (learned from past deliveries; follow them):\n" + "\n".join(f"- {t}" for t in texts)
            if texts else "")


def _stamp(argv: list[str], conventions: str) -> list[str]:
    """Append the project's saved conventions to a create argv's --body."""
    if not conventions:
        return argv
    i = argv.index("--body") + 1
    return [*argv[:i], f"{argv[i]}\n\n{conventions}", *argv[i + 1:]]


def live_qa_section(build_id: str, login: dict | None) -> str:
    """The verify card's LIVE QA section. The test account goes to a 0600 file, never into the card."""
    qa_dir = HERMES_HOME / "delivery" / "qa" / build_id
    text = QA_NO_LOGIN
    if login and (login.get("username") or login.get("password")):
        qa_dir.mkdir(parents=True, exist_ok=True)
        path = qa_dir / "login.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({k: str(login.get(k) or "") for k in ("username", "password", "notes")}, fh)
        text = QA_LOGIN_FILE.format(path=path)
    return LIVE_QA_BRIEF.format(build=build_id, login=text, shots=qa_dir / "shots")


def _add_body(argv: list[str], extra: str) -> list[str]:
    i = argv.index("--body") + 1
    return [*argv[:i], f"{argv[i]}\n{extra}", *argv[i + 1:]]


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
    base_title = m[2] if m else title.removeprefix("Verify: ")
    delivery = delivery_of(verify_id, task["body"])
    rounds, failed = verify_rounds(verify_id, title, task["body"])
    if failed >= MAX_FIX_ROUNDS:
        return json.dumps({"ok": False, "error": f"round limit ({MAX_FIX_ROUNDS}) reached: block your card "
                           "with the failures so the user decides"})
    build = _verify_build(verify_id)
    if build and build["status"] not in ("done", "archived"):  # a parallel check: still in its review round
        posted = _hermes("kanban", "comment", build["id"], f"VERIFY FAILED ({verify_id}, run beside this review):\n"
                         f"{failures}\nReviewer: a High finding unless its failing command passes at HEAD. "
                         "Implementer: fix it with the review findings.")
        if posted.returncode == 0:
            _journal("verify.failed_into_review", build["id"], verify=verify_id)
            return json.dumps({"ok": True, "routed_to": build["id"], "next": (
                "the failures joined the open review round; complete your verify card with the failure evidence")})
    worktree = _parent_worktree(verify_id) or (build and build["workspace_path"])  # released: no link left
    if not worktree:
        return json.dumps({"ok": False, "error": "no parent worktree found; block your card with the failures"})
    request = _request_of(task["body"])
    project = task["project_id"] or ((build and _task_row(build["id"])) or {"project_id": ""})["project_id"] or ""
    state = _branch_state(worktree)
    conventions = _conventions(project, state["pr_base"])
    live_qa = LIVE_QA_RE.search(task["body"] or "")

    def edit(step, argv, chain):
        if step.key == "fix":
            return _stamp(argv, conventions)
        return _add_body(argv, live_qa.group(0)) if live_qa else argv  # the re-verify tests the fixed screens again

    chain, error = run_flow("fix", base_title, request, project, parent=verify_id, worktree=worktree, edit=edit,
                            delivery=delivery,
                            after=lambda step, tid: step.key == "verify" and not live_qa and _set_effort(tid, "low"),
                            round=rounds + 1, source="the verifier ran the reviewed change and it failed",
                            reporter="from the verifier", failures=failures, **state)
    if error:
        return error
    fix_id, reverify_id = chain["fix"], chain["verify"]
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
    base_title = re.sub(r"^(Fix \d+: )+", "", task["title"] or "")
    if args.get("design") and not args.get("picked_design"):
        return design(args, base_title, f"{request}\n\nTHE USER TESTED THE PR AND WANTS:\n{failures}", worktree)
    state = _branch_state(worktree)
    delivery = delivery_of(source_id, task["body"])
    rounds = max(_fix_rounds(delivery)[0], len(re.findall(r"^Fix \d+:", task["title"] or "")))
    waiting = _waiting_fix(base_title, task["project_id"], delivery)
    if waiting:  # one round takes every correction sent before it starts: no second fix + verify chain
        added = _hermes("kanban", "comment", waiting, f"ADDED REQUEST from the user's testing (handle it in this round):\n{failures}")
        if added.returncode == 0:
            _journal("fix.merged", waiting, source=source_id)
            return json.dumps({"ok": True, "task_id": waiting, "merged_into": waiting, "fix_of": source_id,
                               "next": "added to the fix card that has not started yet; it handles both in one round"})
    picked = args.get("picked_design")
    project = task["project_id"] or ""
    conventions = _conventions(project, state["pr_base"])

    def edit(step, argv, chain):
        argv = _add_body(argv, picked) if picked else argv
        return _stamp(argv, conventions) if step.key == "fix" else argv

    def after(step, tid):
        if step.key == "fix":  # before the verify is created: it copies the fix card's followers
            _copy_subscriptions(source_id, tid)
        else:
            _set_effort(tid, "low")

    # the user is the tester of their own feedback; a verifier only when asked
    chain, error = run_flow("fix", base_title, request, project, worktree=worktree, edit=edit, after=after,
                            delivery=delivery,
                            skip=() if args.get("verify") else ("verify",), round=rounds + 1,
                            source="the user tested the PR and found a problem", reporter="from the user's testing",
                            failures=failures, **state)
    if "fix" not in chain:
        return error
    fix_id, verify_id = chain["fix"], chain.get("verify")
    subscribed = all([_subscribe_calling_chat(t) for t in (fix_id, verify_id) if t])
    return json.dumps({"ok": True, "task_id": fix_id, "verify": verify_id, "fix_of": source_id,
                       "worktree": worktree, "chat_subscribed": subscribed,
                       "next": "implementer fixes on the same branch and pushes (the PR updates); reviewer "
                               "re-checks the new change" + (", then the verifier runs the tests" if verify_id else "")
                               + "; you get a message when it is ready to test again. Send further corrections "
                               "with fix_of while it waits: they join this round"})


def design(args: dict, title: str, request: str, code) -> str:
    """Three designers mock up directions A/B/C in parallel; nothing is built until delivery_design_pick."""
    key = "d_" + secrets.token_hex(4)
    assignee = _step_role(FLOWS["design"][0])
    refs = (args.get("design_refs") or "").strip() or "none"
    code = (f"{code} (look, never change: the build happens after the pick)" if code else
            "no repository found: design from the REQUEST and REFERENCES")
    cards, error = run_flow("design", title, request, "", refs=refs, code=code, key=key)
    if error:
        return error
    path = HERMES_HOME / "delivery" / "designs" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the args may hold the QA test account
    with os.fdopen(fd, "w") as fh:
        json.dump({"args": {k: v for k, v in args.items() if k not in ("design", "design_refs")}, "cards": cards}, fh)
    _journal("design.started", cards["A"], key=key)
    return json.dumps({"ok": True, "design": key, "cards": cards, "assignee": assignee,
                       "chat_subscribed": all([_subscribe_calling_chat(t) for t in cards.values()]),
                       "next": "three designers mock it up in parallel (A: closest to the app, B: the user's "
                               "references, C: a fresh take); each sends its HTML file and screenshots when done. "
                               "Show the user all three, ask which one and any mix (\"A with B's header\"), then call "
                               f"delivery_design_pick with design={key}. Nothing is built before that."})


def design_pick(args: dict, **_kw) -> str:
    """The user picked a mockup: submit the build (or fix) with the picked design on every card."""
    ref, letter = str(args.get("design") or "").strip(), str(args.get("choice") or "").strip().upper()[:1]
    if re.fullmatch(r"t_[0-9a-f]+", ref):
        m = DESIGN_SET_RE.search((_task_row(ref) or {"body": ""})["body"] or "")
        ref = m[1] if m else ref
    path = HERMES_HOME / "delivery" / "designs" / f"{ref}.json"
    if not re.fullmatch(r"d_[0-9a-f]+", ref) or not path.exists():
        return json.dumps({"ok": False, "error": f"no design set {ref}: pass the design key or a Design card id"})
    pending = json.loads(path.read_text())
    if pending.get("picked"):
        return json.dumps({"ok": False, "error": f"already picked {pending['picked']}: send changes with fix_of"})
    card = pending["cards"].get(letter)
    if not card:
        return json.dumps({"ok": False, "error": "choice is A, B or C"})
    files, summary, status = _design_output(card)
    if status != "done":
        return json.dumps({"ok": False, "error": f"Design {letter} ({card}) is {status}, not done: wait for it"})
    others = {k: _design_output(t)[0] for k, t in pending["cards"].items() if k != letter}
    notes = (args.get("notes") or "").strip()
    listing = "\n".join(f"- {f}" for f in files) or f"- (no file kept: see kanban_show {card})"
    if notes:  # "A with B's header": the builder can open the mockups the notes mention
        listing += "".join(f"\nDesign {k} (for the notes): " + (", ".join(fs) or f"kanban_show {pending['cards'][k]}")
                           for k, fs in others.items())
    section = PICKED_DESIGN.format(letter=letter, files=listing, summary=summary.strip()[:1500] or "(none)",
                                   notes=notes or "none")
    # force: the duplicate check ran before the designs, and an unfinished one would match now
    result = json.loads(submit({**pending["args"], "picked_design": section, "force": True}))
    if result.get("ok"):
        pending["picked"] = letter
        path.write_text(json.dumps(pending))
        _journal("design.picked", card, key=ref, choice=letter, notes=bool(notes))
    return json.dumps({**result, "picked": letter})


def _design_output(task_id: str) -> tuple[list[str], str, str]:
    """(kept files, result text, status) of a design card: Hermes copies the files it named to attachments."""
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        try:
            status, result = conn.execute("SELECT status, coalesce(result, '') FROM tasks WHERE id = ?",
                                          (task_id,)).fetchone() or ("missing", "")
            try:
                files = [r[0] for r in conn.execute("SELECT stored_path FROM task_attachments WHERE task_id = ? "
                                                    "ORDER BY id", (task_id,))]
            except sqlite3.Error:
                files = []
        finally:
            conn.close()
    except sqlite3.Error:
        return [], "", "missing"
    return files, result, status


def _waiting_fix(base_title: str, project, delivery: str = "") -> str | None:
    """A fix card for the same delivery (or, made before deliveries were named, title) that no worker has started."""
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        rows = conn.execute("SELECT id, title, body FROM tasks WHERE status IN ('todo','ready') AND created_by = "
                            f"'delivery_submit' AND {step_sql()} = 'fix' AND coalesce(project_id,'') = ? "
                            "ORDER BY created_at", (project or "",)).fetchall()
        conn.close()
    except sqlite3.Error:
        return None
    return next((tid for tid, title, body in rows if delivery and f"DELIVERY: {delivery}" in (body or "")
                 or re.sub(r"^(Fix \d+: )+", "", title or "") == base_title), None)


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
    return {"fix_base": head, "pr_base": pr_base,
            "changed": "\n".join(stat.splitlines()[-40:]) or "(unknown: run git diff --stat against the base)"}


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
        row = conn.execute(f"SELECT title, body, project_id, {step_sql()} AS step FROM tasks WHERE id = ?",
                           (task_id,)).fetchone()
        conn.close()
        return row
    except sqlite3.Error:
        return None


def _verify_build(verify_id: str):
    """The build or fix card a verify checks (its parent, or its `BUILD CARD:` line once released)."""
    from . import checks
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        try:
            row = checks.build_of(conn, verify_id)
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return dict(zip(("id", "status", "workspace_path", "title", "body"), row)) if row else None


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
    """Live cards working in `worktree`: on it directly, or a verify card whose parent built there.
    A card stuck behind a blocked/triage parent is skipped: waiting on it would deadlock the new card."""
    try:
        conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        rows = conn.execute(
            f"SELECT id FROM tasks t WHERE status IN ({','.join('?' * len(LIVE))}) AND (workspace_path = ? "
            f"OR ({step_sql('t')} = 'verify' AND id IN (SELECT l.child_id FROM task_links l JOIN tasks p "
            "ON p.id = l.parent_id WHERE p.workspace_path = ?))) AND NOT EXISTS (SELECT 1 FROM task_links l "
            "JOIN tasks p ON p.id = l.parent_id WHERE l.child_id = t.id AND p.status IN ('blocked', 'triage')) "
            "ORDER BY created_at", (*LIVE, worktree, worktree)).fetchall()
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
    for parent in {argv[i + 1] for i, a in enumerate(argv) if a == "--parent"}:  # whoever follows the chain
        _copy_subscriptions(parent, task_id)  # hears this card too, even when no chat called the tool
    return task_id, None


VERIFY_FAILED_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delivery_verify_failed",
        "description": ("Rarely needed: cpipe opens the fix round itself when a verify card completes with "
                        "VERDICT: FAIL. Opens a fix card in the same worktree and a new verify card after it."),
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

DESIGN_PICK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delivery_design_pick",
        "description": ("The user picked one of the three design mockups from delivery_submit(design=true): "
                        "starts the build (or fix) with the picked design on every card. Show the user all three "
                        "files first and pass their choice and any mix in their words."),
        "parameters": {
            "type": "object",
            "properties": {
                "design": {"type": "string", "description": "The design key delivery_submit returned (d_...) or any Design card id"},
                "choice": {"type": "string", "enum": ["A", "B", "C"], "description": "The mockup the user picked"},
                "notes": {"type": "string", "description": "Changes the user wants on top of it, e.g. \"with B's header, no dark mode\""},
            },
            "required": ["design", "choice"],
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
            "design=true first has three designers mock up different looks in parallel; the build starts "
            "only after the user picks one (delivery_design_pick). "
            "Returns the card id; progress and the result come back to this chat."),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short imperative card title"},
                "request": {"type": "string", "description": "A brief, not the conversation: the goal, acceptance criteria, and any file, route, or area the user named. Workers see only this. Max 3000 chars. If an acceptance criterion needs a guess about product behavior (which screen, which users, error/empty cases, what stays unchanged), ask the user with clarify first."},
                "project": {"type": "string", "description": "Hermes project slug (see `hermes project list`), e.g. my-app"},
                "size": {"type": "string", "enum": ["content", "small", "large"], "description": "content: text/copy/docs only. large when the change crosses a boundary (UI -> API -> DB, sockets/events, auth/permissions/consent, persistence or migrations, jobs) or spans more than one package: it gets a mapped, reviewed plan before the build. small: one area, no boundary."},
                "kind": {"type": "string", "enum": ["feature", "bug"], "description": "bug when the user reports something broken or wrong (an error, missing or wrong data, a regression): the implementer reproduces it and finds the root cause before fixing. For a bug the request quotes the symptom and says where (page/route, which users or records), expected vs actual, and since when if known."},
                "force": {"type": "boolean", "description": "Only when the user confirmed this is new work although an open card looks the same"},
                "verify": {"type": "boolean", "description": "Default true for new small/large work: a verify card runs the tests after review; false only when the user asks to skip it. With fix_of the default is false (the user re-tests); true only when the user asks for a verifier"},
                "live_qa": {"type": "boolean", "description": "UI changes only. Before submitting one, ask the user once (clarify): should the verifier test it live in the browser, and if the app needs a login, which test account? true only when they say yes. The verifier starts the app, logs in, checks the changed screens at desktop and mobile width, and reports breaks as failures and design issues as notes."},
                "qa_login": {"type": "object", "description": "With live_qa: the test account the user gave (never a real person's account, never guessed). Stored in a private file, not on the card.", "properties": {"username": {"type": "string"}, "password": {"type": "string"}, "notes": {"type": "string", "description": "Login URL, 2FA or role notes the user gave"}}},
                "fix_of": {"type": "string", "description": "Card id or PR URL the user tested and found broken; request = what is wrong"},
                "design": {"type": "boolean", "description": "UI work where the look is the open question (a new screen, a redesign, a layout the user describes loosely, or a fix_of saying it looks wrong): three designers first mock up directions A (closest to the app), B (the user's references), C (a fresh take) as HTML files, and nothing is built until the user picks one with delivery_design_pick. Not for copy, a small style tweak, or a UI the user already specified exactly."},
                "design_refs": {"type": "string", "description": "With design: what the user wants it to look like, in their words: products or sites to resemble, screenshot or image paths they sent, colors, things they dislike now"},
                "branch": {"type": "string", "description": "New work: the branch to create, named by your project conventions (memory). Omit for Hermes's default. Not for fix_of."},
                "base": {"type": "string", "description": "New work: the branch to start from and open the PR against when the user names one (\"branch out from X\"), e.g. IC-develop. Omit for the project's base branch. Not for fix_of: a fix follows its PR's base."},
            },
            "required": ["request"],
        },
    },
}
