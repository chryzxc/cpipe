# cpipe: the plan step

Steps 1, 2, 4 and 5 of `cpipe:run` (step 3 is `steps/design.md`). Spend the reading here, once, so the build does
not repeat it.

## 1. Set up

1. **Repository and base.** `git rev-parse --show-toplevel`, `git remote -v`, `git status --short --branch`, the
   default branch. If the task names a repository, path or base branch, use it; if several repositories match,
   pick the one the conversation used. Fetch the base so it is current.
2. **Branch name.** Name the branch the way the project does: its instructions, your skills, or the pattern of
   its recent branches (`git branch -r --sort=-committerdate | head -20`), such as `<area>/fix/<slug>`. With no
   pattern, use `<type>/<short-slug>` with the type of the change (`fix`, `feat`, `refactor`, `chore`). Pull
   request tooling often reads the type from the branch name, so it must match the change: if step 2 finds a
   different kind, rename the branch with `git branch -m` before anything is pushed.
3. **Worktree.** A new branch and worktree from the base, beside the repository:
   `git worktree add -b <branch> <repo>-worktrees/<branch with / replaced by -> <base>`. Follow the project's own
   worktree convention instead if its instructions name one. If the name exists, add a suffix; never reuse or
   reset someone else's branch.
4. **Dependencies.** Install them inside the worktree the way the project does (its lockfile and CI show how):
   `npm ci`, `pnpm install --frozen-lockfile`, `yarn install --immutable`, `uv sync`, `pip install -e .` into
   the project's virtual environment. Never install globally; do not change the lockfile unless the task is about
   dependencies. Copy local env files (`.env`) from the user's checkout only if the checks need them, and never
   print their contents.
5. **Run log.** Start it (`cpipe:run`) with the task, the base, the worktree and the roles line.
6. **Tidy up.** Remove this repository's finished cpipe worktrees so they do not fill the disk. A worktree from
   `git worktree list` is cpipe's when its git dir holds a run log (`$(git -C <path> rev-parse --git-dir)/cpipe/run.md`);
   never touch any other worktree, the user's checkout or this run's worktree. Remove one only when all hold:
   - a merged pull request has its `HEAD` as the head commit
     (`gh pr list --head <branch> --state merged --json number,headRefOid`); a branch with no merged pull
     request stays, even when it looks merged, because a run that has not committed yet looks the same;
   - `git status --porcelain` is empty, apart from tracked files that match the ignore rules
     (`git ls-files -ci --exclude-standard`, such as runtime logs), which you restore with `git restore` first;
   - no process has its working directory inside it (`lsof -d cwd -Fn`).

   Then `git worktree remove <path>` (never `--force`) and `git worktree prune`; the branch stays. Name what was
   removed in the run log. Anything unclear stays; a failed removal is noted and never ends the run.

If the install fails, try the next way from `steps/verify.md` and record it; a failed install never ends the run.

## 2. Explore

If the roles file names an `explore` profile, have it map the code (`roles.md` in `cpipe:run`) and read the
lines around each place it cites to confirm. Otherwise do it yourself:

1. **Instructions.** Read the project's agent and contributor instructions (`AGENTS.md`, `CLAUDE.md`,
   `CONTRIBUTING.md`, the README's development section). They win over these skills where they conflict, except
   for the guard rails in `cpipe:run`. Read the project's and the user's lessons too (`cpipe:run`, Lessons):
   each one is a question this run must answer.
2. **Code.** If you have a code index or code-navigation tool, locate the code with it first. Read the code the
   task touches and the helpers you could reuse. Skip code that is not on a path below.
3. **Flow map.** Trace every path through the code you will change, end to end, so nothing breaks out of sight:
   - **Entrances:** everything that reaches it: routes and endpoints, pages and UI actions, jobs, event and queue
     handlers, CLI commands, other modules and packages. Search for every usage across the whole repository, not
     only the direct callers.
   - **Exits:** everything it produces and who consumes it: responses and their shape, data written, events
     emitted, UI rendered, and the code on the other side that reads them (the client of a server change, the
     server of a client change, other services, shared types, generated clients).
   - **Guards on the way:** authentication, permissions and roles, tenancy, feature flags, validation, rate
     limits. Note which role or state may do what, on both sides (the server's check and what the client shows
     or hides).
   - **Behavior to keep:** what each entrance and consumer does today that must still work after the change.
   Record the map in the run log with paths and line numbers. A path you cannot trace is a risk in the plan.
4. **Premortem.** Imagine the change shipped and something broke that the task never mentioned. List the most
   likely ways, looking through each lens that applies:
   - **Contracts:** API, schema, types, config, file formats, URLs; old clients, old data and old config still
     work.
   - **Data and state:** existing records, migrations, caches, sessions, concurrent or repeated runs, partial
     writes.
   - **Who and where:** each role, tenant, locale, time zone, platform, device, feature-flag state, environment.
   - **Failure paths:** errors, timeouts, retries, empty and huge input, a dependency down.
   - **User-facing:** loading, empty and error states, navigation, accessibility, wording.
   - **Cost:** queries in loops, large inputs, work on every request or render.
   - **The project's lessons** and anything its instructions warn about.
   Each risk gets a check (a test, a command or a QA step), or a one-line reason it cannot happen here, citing
   the code. These are prompts, not a form: find the risks this change has, including ones no lens names.
5. **Checks.** Find the project's own commands: `package.json` scripts, `Makefile`, `pyproject.toml`, the CI
   workflow files (they show how CI installs and tests). Pick the narrowest command that covers the change, plus
   the build or typecheck if the project has one, plus the broader suite for QA.
6. **Baseline.** Run those checks once in the worktree, before any change, the way `steps/verify.md` (Fast
   checks) runs them: in the background, the full suite (not only the narrow tests) and the build one after the
   other, with no serial or single-worker flags. Keep exploring and planning while they run. Verify waits for a
   baseline still running rather than starting the suite beside it; one with no new output for 10 minutes is
   hung: stop it and record it as not finished, and verify checks only its own new failures on the base
   (`steps/verify.md`, New failures). Record each
   result: pass, fail (which tests) or not run here (why). Save the ids of the failing tests and lint errors, one per line and
   sorted, to `baseline-<check>.txt` beside the run log, so verify can compare by name, not by count. A check
   already red is the baseline, not your job, unless the task is to fix it. If a check will not run, work
   through `steps/verify.md` now, so the plan names commands that work.
7. **Kind.** bug, feature, refactor, chore, port, hotfix or review (`cpipe:run`). Load the playbook. If the branch type
   from step 1 does not match the kind, rename the branch now.
8. **Reproduce** (bug and hotfix). Before planning, prove the cause: write the failing test in the worktree (the
   playbook's steps 1 to 3), run it, and record the RED output and the cause it proves in the run log. The plan
   is built on that cause, not a guess; a cause you cannot prove yet is a risk in the plan with the check that
   will prove it.

## 4. Decide the open questions

Never ask the user (`cpipe:run`, Unattended). List what the request leaves open: a product decision, which of
two behaviors is wanted, which base or tenant, an external system you have no access to. Settle each from the
request, the code, the project's history and the conversation: pick the most likely answer, and of two equally
likely ones the more reversible. Each becomes an assumption in the plan with a one-line reason. Something only
the user may do (credentials, destructive actions, an unasked push) is left out of the scope and goes in the
report.

## 5. Plan

If the roles file names a `plan` profile, have it draft the plan (`roles.md` in `cpipe:run`), then check the
draft against the code and the baseline yourself. Write the plan in the chat and in the run log:

- **Goal:** one sentence, in the user's words.
- **Kind** and playbook.
- **Base** (branch and commit) and **worktree**.
- **Scope:** the exact files to change, new ones marked `(new)`. **Non-goals:** what you will not touch.
- **Flow map:** the entrances, exits and guards from explore, in short.
- **Approach:** each idea the change rests on, with why, the alternative avoided and the problem it would have
  caused, and the trade-off. The report's Approach table is built from this and the assumptions.
- **Acceptance:** observable criteria, numbered `A1`, `A2`…, each one checkable, including the design note's
  states.
- **Must not change:** numbered `M1`…; for each entrance, consumer and guard in the map, the behavior that has
  to stay, with the check, test or QA step that proves it. An existing test or check that already covers it is
  named, not duplicated.
- **Risks:** numbered `R1`…; the premortem, each with its check or the reason it cannot happen. A new test only
  where nothing existing would catch the break.
- **Tests:** the new tests, each with the break it catches: the line or behavior that, when broken, makes it
  fail. Before adding one, look for an existing test that already fails on that break; then it covers the item
  and nothing is added. Extend an existing test before writing a new file. A test that fails on no realistic
  break of this change is not planned (rendering a constant, a getter, the framework, a mock returning what it
  was told). Fewer, sharper tests over many.
- **Checks:** the exact commands, with the baseline result of each (or `running` while it is not back).
- **Steps:** the build, in the playbook's order, one behavior per step, written so a builder who has not read
  the code can follow it:
  ```
  S1  <path>  <function, class or line it changes>
      Do: <add | change | remove | move | extract> <exactly what: names, values, conditions>
      Test first: <test file> <test name>, fails now because <reason>; or covered by <existing test>
      Verify: <narrow command from Checks> -> <expected result>
      Covers: A1, M2
  ```
- **Assumptions:** each with its one-line reason from step 4.
- **Deliverable:** a local commit on the delivery branch (default), or a draft pull request if asked.

### Plan gate

Before building, check the plan item by item and fix every no. It is your own check, never a stop:

- Every step's path exists (`git ls-files <path>`) or is marked `(new)`, and every function or class it names is
  found in that file (`grep -n`).
- Every Do line says exactly what changes. None says "as needed", "if needed", "make sure", "improve", "clean
  up", "etc.", "appropriately", "TBD", or leaves a choice open: decisions belong in step 4.
- Every step has a Verify command from Checks with its expected result.
- Every `A`, `M` and `R` is in some step's Covers, or names the existing test or check that covers it (a risk
  may instead give the reason it cannot happen).
- Every Scope file is in a step, and every step's file is in Scope.
- No step leaves a file over 4,500 tokens (its size now, `wc -c` / 4, plus what the step adds) except by the
  import and call into a new file: the new code goes in a new file the step names (`steps/verify.md`, File size).
- Each step depends only on steps before it.

Post the plan in one message together with your next tool call and start building; do not wait for approval.
If the user steers mid-run, revise the plan in the run log and continue. If the user cancels, remove the
worktree and branch you created (nothing is committed on them).

## Rules

- Nothing outside the worktree changes in these steps except the tidy-up; exploring is read-only.
- The plan is the builder's instructions: exact enough to follow step by step without re-reading the code.
- Every acceptance criterion must be checkable by a test, a command or a QA step.
- Every new test must name the break it catches (Tests above); no test is added for coverage alone.
- Never ask the user anything; every open question ends as a recorded assumption.

## Done when

The worktree exists with dependencies installed, the baseline, the flow map and the premortem are recorded, the plan
with its must-not-change list, risks, assumptions and steps passes the plan gate and is in the run log.
