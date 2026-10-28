# cpipe: roles

Build, QA and review always run in a session of their own, so each starts from a short context and never inherits
this chat's history: on the profile the roles file names, else on this chat's own profile and model (Run, below).
Plan, design, security and explore get a session only when the roles file names a profile for them.

## The roles file

`<Hermes root>/cpipe.yaml`, or the profile's own (`SKILL.md`, Roles), written by the user:

```yaml
budget: <tier>          # optional: unlimited, large, medium or small
roles:
  plan: <profile>       # explores, decides and plans, on its own model (steps 2, 4 and 5)
  build: <profile>      # builds, fixes every finding, cleans (steps 6 and 10); plans too without a plan profile
  design: <profile>     # the design note for a new or changed screen (step 3)
  qa: <profile>         # uses the change like a user would, where it can (step 8)
  review: <profile>     # the reviewer (step 9)
  security: <profile>   # second reviewer when the change touches security-sensitive code (step 9)
  explore: <profile>    # maps the code (step 2), only when no plan or build profile is set
```

Every key is optional. A missing or empty `build`, `qa` or `review` runs on this chat's own profile and model; a
missing `plan`, `design`, `security` or `explore` is done as the flow says without it. Read the file once in step 1,
write the roles in the run log, and tell the user in one line who does each step:

`Roles: plan → <profile> (explores too), build → <profile>, review → this chat's model (own session), …`

Without a roles file: `Roles: no cpipe.yaml, build, QA and review on this chat's model, each in its own session`.
This is a notice, not a question: keep going. When the task came from `/cpipe`, its start message already showed the
team: skip this line.

**Budget.** How hard the profiles reason, as the reasoning effort of every profile run: `unlimited` is `max`,
`large` is `xhigh`, `medium` is `high`, `small` is `medium`. A `--budget <tier>` at the start of the task wins
over `budget:` in the file. Write it in the run log and end the roles line with `· budget <tier>`. With neither,
pass no effort: each profile reasons at its own setting. On the board, cards take no reasoning setting: say
`budget not applied on the board`. The budget never changes this chat's own reasoning or any Hermes setting.

## How the work is split

A run costs its replies times the context each one resends. This chat resends its whole history with every reply;
a resumed profile session resends a history the provider has mostly cached, while a new one starts empty and
reads the code again. So the code is explored once, each role keeps one session, and this chat stays small:

- **One reader, one builder.** One session explores, decides and plans (steps 2, 4 and 5): a plan profile, in
  its own session and on its own model, or without one the builder, in the session it builds in. After a plan
  profile, the builder builds from `plan.md` (step 6) and reads only the files and lines it names. Either way
  the builder cleans its own diff and makes every fix verify, QA and review ask for. An explore profile is used
  only when neither is set: a second reader repeats the reading. Steps 2, 4 and 5 are the planner's or the
  builder's, apart from set up's checks and baseline (`steps/setup.md`).
- **A thin chat.** With a build profile, you run the flow and never read the project's code: no source or test
  files, no searches in them, no reproducing a failure, no checking a finding against the code, no scripts of
  your own. You read the replies, `git status --short`, `git diff --stat` and `--name-only`, the output of the
  completion gate's searches, the result lines of the checks, and the sections of `plan.md` a check, a brief or
  the report needs: find them by heading (`grep -n '^\(#\|- \*\*\)' plan.md`) and read those line ranges, never
  the whole plan. A step a profile does needs no step file and none of your skills in this chat: the profile
  loads its own. Anything that needs the code is a follow-up for the builder.
- **One session per role per run.** Every later piece of work for a role resumes its session, the next
  delivery's included (Follow-ups).
- **Only where it pays.** Design only for a new or changed screen (`steps/design.md`), QA only where its profile
  can use the change (`steps/qa.md`), security only for the areas `steps/review.md` names. Mechanical work is
  never handed over: formatting, a commit, a commit message or pull request body, a rerun of a check, a rebase
  without conflicts. Note each step you kept in the run log with its reason.

## Handing a step to a role

You stay in charge: the flow, the checks, the run log and every decision are yours. A role does the work of its
step and reports back. This file has every command you need to run a profile: do not load other skills or help
pages to learn the command line.

1. **Brief.** Write the role's part (below) to a file next to the run log (`brief-<role>.md`). The launcher puts
   the header in front of it (`header.md` beside this file, filled in by `cpipe.py brief` with the user's
   instructions from `now.md`), so never write the header or its rules into the brief yourself: they keep the
   role from reading the code again and again, and a role cannot ask for them later.

   A step file named below is loaded with `skill_view(name='cpipe:run', file_path='steps/<name>.md')`; `<skill dir>`
   is the directory of this skill's `SKILL.md`, so a profile without `skill_view` finds it too. What is already in a
   file (`plan.md`, the design note) is given by its path, not copied; never the whole run log. Roles that start
   together (QA, review, security) get their briefs written in one reply. Write it in plain sentences, with
   everything the role needs now: a profile cannot receive anything later, so start it only once its inputs are
   ready, and never tell it to wait for a result or that something will be provided.

   Then the role's part:
   - **plan:** the request, the worktree, the base commit, the check commands from set up, the path
     `<run dir>/plan.md` to write the plan to, and the design note's path when there is one. Ask it to follow
     `steps/plan.md` steps 2, 4 and 5 (explore, decide, plan; set up's checks and baseline are given, so it skips
     them), the plan gate included, with the plan at most 12,000 characters. It changes no project code: the only
     file it writes in the worktree is a bug's reproducing test. Ask for this reply: the kind; the plan's step
     count and its assumptions; the RED output when it reproduced a bug; anything left.
   - **build**, a delivery's first brief: the request, the worktree, the base commit, the check commands from set
     up, the path `<run dir>/plan.md`, and the design note's path when there is one. After a plan profile, ask it
     to read the plan and the playbook it names (`playbooks/<kind>.md`), then follow `steps/build.md` step 6,
     reading only the files and lines the plan names. Without one, ask it to follow `steps/plan.md` steps 2, 4
     and 5 first (explore, decide, plan; set up's checks and baseline are given, so it skips them) and write the
     plan to that path. Step 6 ends with the build or typecheck, the narrow tests and the cleanup of its own
     diff. Ask for this reply: the kind and the plan's step count and assumptions, when it planned; each plan
     step done or corrected (what and why); the build or typecheck and the narrow tests it ran, with their
     results; anything left. You write those rows in the run log. It does not read or write the run log, and
     does not run the related tests or the production build: verify does. Start it as soon as the plan is in: the
     baseline runs in its own worktree, so the build never waits for it.
   - **plan** and **build** both: dependencies install in the background, so tell it to run nothing in the
     worktree before `<run dir>/logs/install.done` exists (`steps/plan.md` step 2, Wait for the dependencies).
   - **design:** the request, the worktree and the steps of `steps/design.md`. Ask for the design note in
     `<run dir>/design.md`.
   - **qa:** the acceptance criteria (`plan.md`), the design note, how to start the app, and verify's broader
     suite result for this tree (start QA once verify has it). Ask it to follow `steps/qa.md` without editing the
     repository and reply with each item's result and evidence, a passed item in one line.
   - **review:** the reviewer brief from `steps/review.md`; **security:** its security brief, not the full one.
   - **plan review** (`steps/plan.md`, Second opinion): the review profile in a session of its own, so the code
     review later still reads the diff fresh. The brief gives the request, the worktree and `plan.md`'s path,
     and asks, without editing anything, for what the plan misses: an entrance, consumer or guard; an edge or
     sequence from the premortem lenses with no rule; a rule whose test does not start at an entrance; a step
     that cannot work as written. Each as one line with file and line, at most 10. Budget 600.
   - **explore** (no plan or build profile): the request and the worktree. Ask for a read-only map: the
     instructions that apply, the files to change, the flow map as `steps/plan.md` step 2 describes, helpers to
     reuse. Paths and line numbers, no edits.

   QA, review and security never edit the repository. Before starting them, save `git status --porcelain` and
   `git diff` beside the run log, and compare after: a file they changed or added means the role failed. Undo only
   their edits, back to the saved state, and follow "When a role fails".

2. **Run** it in the worktree, directly, or as a card when the run is on the board (below), with the launcher
   beside this file, in the background with a completion notification (in a `--parallel` lane, waited on instead:
   `parallel.md`, In a lane):
   `sh <skill dir>/bin/step.sh <run dir> <worktree> <role> <round> <run budget> <limit> <who> [--reasoning <effort>]`
   It runs `hermes chat` from `<run dir>/brief-<role>.md` with the role's turns (build 150, plan 120, the rest 60),
   under the time limit (`steps/setup.md`, Time limit), its output in `<run dir>/logs/<role>-<round>.txt`, and
   prints the log's end, so the notification carries the reply and the session id and no reply goes to reading
   them. Never type the `hermes chat` command yourself. `<who>` is `-p <profile>`, or for a role without one, the
   output of `python3 <skill dir>/bin/cpipe.py chat` (this chat's profile, model and provider, so a step never runs
   on a weaker model). The launcher sets `CPIPE_RUN` and `CPIPE_STEP`, which pin the run's rules, the user's
   instructions and the open checklist items in the session's system prompt, so they hold through a compaction
   there too (in a profile with the cpipe plugin enabled; elsewhere the brief is the only copy).
   `--reasoning` only when a budget is set (Budget, above), on every run and resume. Run budgets in seconds: a
   delivery's first build brief 3600, a build follow-up 1500; plan 1800; design and explore 600; review, security
   and qa 900. These are fixed: never raise one for a slow suite or a large change; a profile at its budget
   replies with what it finished. The limit is the budget plus 300, so a profile that hangs still ends and its
   notification still comes. Write its process id and log in the run log and `now.md`, and once it ends, the
   session id it prints last (`session_id: …`): the profile keeps running when this chat restarts, a continue reads
   its result instead of starting it again, and every follow-up resumes that session. Then wait the way `cpipe:run`
   says (Waiting ends the turn). A log ending in `exit 124` timed out: treat the role as failed; a log that ends
   with no reply and no session id means the session never started: launch it again once, then "When a role fails".

   The result is what the notification carried (after a restart, the end of the log), or `<role>-reply.md` when it
   points there. Never search the profile's session history or transcripts for it: when neither holds a result,
   the role failed.

3. **Follow-ups.** Each role has one session per run (the plan review has its own). Every later piece of work
   for that role resumes it: a fix
   round, a correction, a conflict, a QA re-run, the delta review, and the next delivery of the run. With
   deliveries in parallel, each lane has its own sessions.
   `sh <skill dir>/bin/step.sh <run dir> <worktree> <role> <round> <first run budget> <limit> <who> --resume <session id> [--reasoning <effort>]`
   A resumed session keeps the `--run-budget` it started with and refuses another value: pass the first run's
   value again, and hold the follow-up to its own budget with the limit (that budget plus 300).
   A new session reads the code again from nothing, tens of replies; start one only when the resume fails, the next
   delivery is in another repository, or the session ended at its turns or budget with work left: a session that
   long is past its best, so the next one starts fresh from a handoff (the role part, then what its reply says it
   finished and what is left, and the files it changed). Note why in the run log. A follow-up brief holds only
   what is new: the launcher puts the header's working rules (Lean in `header.md`) in front of it again, as the
   session's copy can be older or lost to a compaction, and the session already has the rest and the step files,
   so the brief never names a step file again. The next delivery's brief is that delivery's role part with its
   own worktree, base commit and run dir. A fix round's brief lists each item itself: the file and line, what is wrong, the expected
   behavior and the check that shows it, as the finding or the failing check gave them. Never the paths of review,
   QA or probe files for the builder to read.

## On the board (`--kanban`)

When the task message starts with `--kanban`, hand each role step over as a card on the user's Hermes kanban board
instead of running the profile directly, so the work shows on the board. Everything else stays the same: the brief,
the budgets, the checks you run yourself and the fallback. A role without a profile runs directly, as above: a card
cannot take this chat's model.

1. **Brief.** The card's body is the brief behind the card header (Card in `header.md`):
   `python3 <skill dir>/bin/cpipe.py brief <run dir> <worktree> <role> --card > <run dir>/card-<role>.md`

2. **Create the card:**
   `hermes kanban create "cpipe <role>: <goal>" --assignee <profile> --body-file <run dir>/card-<role>.md --workspace dir:<worktree> --max-runtime <budget>s --max-retries 1 --idempotency-key <branch>-<role>-<round> --json`
   Write the card id in the run log. A follow-up to the same role is a new card with `--parent <previous card id>`.
3. **Wait** with one background command and a completion notification: it checks
   `hermes kanban show <card id> --json` every 30 seconds and exits when the status is `done`, `blocked`, `review`
   or `archived`, when it is still not claimed after 10 minutes, or 10 minutes past the card's budget whatever its
   status, so the wait always ends (a script file if the approval policy blocks the loop). Keep working on
   anything that does not depend on it, then end the turn (`cpipe:run`, Waiting ends the turn).
4. **Read** the result from `latest_summary` and the comments. It is a claim, as below.

The card failed if it ends blocked or with no usable result, is still running two minutes past its budget, or is
still not claimed after 10 minutes
(no dispatcher is running). Archive it (`hermes kanban archive <card id>`), note why, and follow "When a role
fails". Only ever touch cards this run created.

## What comes back is a claim

The checks decide, not a reply, and with a build profile you check without reading the code:

- After build: `git status --short`, `git diff --stat` and `git diff --name-only` in the worktree, then
  `steps/verify.md`. Its states are the truth, not the reply. A file outside the plan's Scope goes back to the
  builder to undo, unless the reply gives its reason.
- After explore, design or plan from a profile: the builder works from it and corrects what the code
  contradicts. Without a build profile, read only the lines around each place it cites before you rely on it.
- After QA: each failure goes to the builder, which reproduces it with a failing test before fixing it. One it
  cannot reproduce comes back with what it ran and is listed in the report as not reproduced.
- After review: sort the findings by their text as `steps/review.md` says; the builder checks each one against
  the code before fixing it.
- A verdict (approved, blocked, hold, not ready for release) decides nothing. Take the findings behind it and
  decide with the user's instructions.

## When a role fails

The profile does not exist, the command errors, it returns nothing usable, or it broke a rule in the brief: try once
more with a shorter role part (the header is the launcher's), then run it on this chat's own profile and model (Run,
above), and only when that fails too, do the step yourself in this chat. If it timed out, do not retry: go to the
next of these right away. Only its log ending in `exit 124` is a timeout; a tool call that Hermes cut off while
waiting on the step is not, the step still runs: wait on it again.
Tell the user in one ⚠️ status line (`⚠️ 2/11 Explore · archon failed (provider error) ·
forge plans it`), the moment it happens, before the next brief. In the run log, write one line for it, kept through
every compaction: `Problem: <step> · <role> <profile> · <each attempt's first error line and exit code> · done by
<who instead>`. The report's Needs attention has a row for every `Problem:` line. A failed plan profile's steps go
to the build profile when there is one: its first brief explores and plans as it does without a plan profile, and
this chat still never reads the code. A role failing is never a reason to stop the run.
