# cpipe: roles

Optional. Without a roles file, you run every step yourself in this chat.

## The roles file

`<cpipe home>/cpipe.yaml` (`SKILL.md`, Roles), written by the user:

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

Every key is optional; a role that is missing or empty is done by you in this chat. Read the file once in step 1,
write the roles in the run log, and tell the user in one line who does each step:

`Roles: plan → <profile> (explores too), build → <profile>, design → this chat (no profile set), …`

Without a roles file: `Roles: no cpipe.yaml, every step runs in this chat`. This is a notice, not a question:
keep going.

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

1. **Brief.** Write it to a file next to the run log (`brief-<role>.md`). Start with this header, word for word:
   fill in the `<…>` parts and never shorten, reword or drop a line. Its rules keep the role from reading the code
   again and again, and a role cannot ask for them later.

   ```
   Direct request from a delivery run. No task board, no cards: do this work and reply with the result.
   Work only in <worktree>. Do not commit, push, merge, open pull requests or message anyone.
   Do not skip or weaken tests. Never print secrets or .env contents.
   A failure that also happens without this change is the baseline: note it and keep going. Never stop for a
   decision: choose within the request's scope and say what you chose.
   The user's instructions for this work, word for word; they settle the questions they answer:
   <each instruction from now.md>
   Report findings with their evidence and the fix you suggest, never a verdict such as hold, blocked or not
   ready for release: the delivery run decides.
   Use your own skills for this kind of work where you have them.
   Keep it lean: the project's own dev server, test runner and tools first; evidence is the command and its
   result; no hash manifests, integrity receipts or copies of the tree. At your time budget, stop and reply
   with what you have and what is left.
   Every reply resends all you have read, so read little: the lines you need (search first, a whole file only
   when you need all of it), never a file you already have, never a file again to check an edit the tool
   reported applied. Work in few replies: independent reads, searches, edits and commands go together in one
   reply. A change and its check are one reply, not two: all its edits, then the command that checks it. Calls
   in one reply run in the order given, so the check sees the edits: never wait for an edit's result before
   sending its check. Test and build output goes to a file in <run dir>/logs/, and the same command prints the
   exit code and only the summary and failure lines, so no reply goes to reading the file.
   Load only the cpipe files named below, each as skill_view(name='cpipe:run', file_path='<file>') (without
   skill_view, read <skill dir>/<file> with your file tool), once per session (again only when its text has left
   your context); never load cpipe:run's SKILL.md itself: that is the delivery run's own procedure, not yours.
   Skip the project's general guides (architecture, standards, contributing) unless your step says to read them.
   Never list or poll processes. Write only the files your work needs and your reply file, never the run log,
   now.md, receipts or helper scripts around the project's own commands.
   Reply in at most 20 lines: the result, the evidence, what is left. Only what does not fit goes in
   <run dir>/<role>-reply.md, and you give its path: findings, failures and corrections, each in at most 5 lines
   (a finding: file and line, what is wrong, why it matters, the fix, the check that shows it). Never what passed,
   what you did not do or the brief restated.
   ```

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
     does not run the full suite or the production build: verify does. Start it as soon as the plan is in: the
     baseline runs in its own worktree, so the build never waits for it.
   - **plan** and **build** both: dependencies install in the background, so tell it to run nothing in the
     worktree before `<run dir>/logs/install.done` exists (`steps/plan.md` step 2, Wait for the dependencies).
   - **design:** the request, the worktree and the steps of `steps/design.md`. Ask for the design note in
     `<run dir>/design.md`.
   - **qa:** the acceptance criteria (`plan.md`), the design note, how to start the app, and verify's broader
     suite result for this tree (start QA once verify has it). Ask it to follow `steps/qa.md` without editing the
     repository and reply with each item's result and evidence, a passed item in one line.
   - **review / security:** the reviewer brief from `steps/review.md`.
   - **explore** (no plan or build profile): the request and the worktree. Ask for a read-only map: the
     instructions that apply, the files to change, the flow map as `steps/plan.md` step 2 describes, helpers to
     reuse. Paths and line numbers, no edits.

   QA, review and security never edit the repository. Before starting them, save `git status --porcelain` and
   `git diff` beside the run log, and compare after: a file they changed or added means the role failed. Undo only
   their edits, back to the saved state, and follow "When a role fails".

2. **Run** it in the worktree, directly, or as a card when the run is on the board (below):
   `hermes -p <profile> chat -Q --query-file <brief> --in <worktree> --run-budget <seconds> [--reasoning <effort>]`
   `--reasoning` only when a budget is set (Budget, above), on every run and resume. Budgets in seconds: a
   delivery's first build brief 3600, a build follow-up 1500; plan 1800; design and explore 600; review, security
   and qa 900. These are fixed: never raise one for a slow suite or a large change; a profile at its budget
   replies with what it finished. Use your terminal's background mode with a completion notification when it
   has one; otherwise a timeout above the budget. Write its process id and output file in the run log and
   `now.md`, and once it ends, the session id it prints last (`session_id: …`): the profile keeps running when
   this chat restarts, a continue reads its result instead of starting it again, and every follow-up resumes that
   session. Then wait the way `cpipe:run` says (Waiting ends the turn). The budget is a hard limit: run the
   profile under the time limit (`steps/setup.md`, Time limit) at its budget plus 5 minutes,
   `perl <run dir>/limit.pl <seconds> hermes -p <profile> chat …`, so a profile that hangs still ends and its
   notification still comes. Its output goes to its own log, `<run dir>/logs/<role>-<round>.txt`, and the same
   command then prints the log's end, so the notification carries the reply and the session id and no reply goes
   to reading them: `perl … > <log> 2>&1; echo "exit $?" >> <log>; tail -n 30 <log>`.
   One the limit ended (`exit 124`) timed out: treat the role as failed.

   The result is what the notification carried (after a restart, the end of the log), or `<role>-reply.md` when it
   points there. Never search the profile's session history or transcripts for it: when neither holds a result,
   the role failed.

3. **Follow-ups.** Each role has one session per run. Every later piece of work for that role resumes it: a fix
   round, a correction, a conflict, a QA re-run, the delta review, and the next delivery of the run. With
   deliveries in parallel, each lane has its own sessions.
   `hermes -p <profile> chat -Q --resume <session id> --query-file <brief> --in <worktree> --run-budget <seconds> [--reasoning <effort>]`
   A new session reads the code again from nothing, tens of replies; start one only when the resume fails or the
   next delivery is in another repository, and note why in the run log. The follow-up brief starts with the
   header's lines from `Every reply resends` to `reading the file.`, word for word: the session's copy of them can
   be older than this file or lost to a compaction. Then only what is new: the session already has the rest of
   the header and the step files. The next delivery's brief is that delivery's role part with its own worktree,
   base commit and run dir. A fix round's brief lists each item itself: the file and line, what is wrong, the
   expected behavior and the check that shows it, as the finding or the failing check gave them. Never the paths
   of review, QA or probe files for the builder to read.

## On the board (`--kanban`)

When the task message starts with `--kanban`, hand each role step over as a card on the user's Hermes kanban
board instead of running the profile directly, so the work shows on the board. Everything else stays the same:
the brief, the budgets, the checks you run yourself and the fallback.

1. **Brief.** Start the brief with this instead of the first line of the header above:

   ```
   Card from a delivery run. This brief is your procedure and outranks your usual card rules.
   When done, complete this card with your full result as the summary:
   hermes kanban complete <card id> --summary "<result>"
   Whatever the outcome, complete the card: do not block it, request changes or create other cards.
   ```

2. **Create the card:**
   `hermes kanban create "cpipe <role>: <goal>" --assignee <profile> --body-file <brief> --workspace dir:<worktree> --max-runtime <budget>s --max-retries 1 --idempotency-key <branch>-<role>-<round> --json`
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

The profile does not exist, the command errors, it returns nothing usable, or it broke a rule in the brief: try
once more with a shorter role part (the header stays whole), then do the step yourself in this chat. If it timed
out, do not retry: do the step yourself right away. Tell the user in one line
(`build: <profile> failed (<reason>), running it in this chat`) and note it in the run log and the report. A failed
plan profile's steps go to the build profile when there is one: its first brief explores and plans as it does
without a plan profile, and this chat still never reads the code. A role failing is never a reason to stop the run.
