# cpipe: roles

Optional. Without a roles file, you run every step yourself in this chat.

## The roles file

`${HERMES_HOME:-~/.hermes}/cpipe.yaml`, written by the user:

```yaml
budget: <tier>          # optional: unlimited, large, medium or small
roles:
  explore: <profile>    # maps the code (step 2)
  design: <profile>     # the user-facing design note (step 3)
  plan: <profile>       # drafts the plan (step 5)
  build: <profile>      # writes the tests and the code, and the cleanup (steps 6 and 10)
  qa: <profile>         # uses the change like a user would (step 8)
  review: <profile>     # the reviewer (step 9)
  security: <profile>   # second reviewer when the change touches security-sensitive code (step 9)
```

Every key is optional; a role that is missing or empty is done by you in this chat. Read the file once in step 1,
write the roles in the run log, and tell the user in one line who does each step:

`Roles: explore → <profile>, design → this chat (no profile set), plan → <profile>, build → <profile>, …`

Without a roles file: `Roles: no cpipe.yaml, every step runs in this chat`. This is a notice, not a question:
keep going.

**Budget.** How hard the profiles reason, as the reasoning effort of every profile run: `unlimited` is `max`,
`large` is `xhigh`, `medium` is `high`, `small` is `medium`. A `--budget <tier>` at the start of the task wins
over `budget:` in the file. Write it in the run log and end the roles line with `· budget <tier>`. With neither,
pass no effort: each profile reasons at its own setting. On the board, cards take no reasoning setting: say
`budget not applied on the board`. The budget never changes this chat's own reasoning or any Hermes setting.

## Handing a step to a role

You stay in charge: the flow, the checks, the run log and every decision are yours. A role does the work of its
step and reports back. This file has every command you need to run a profile: do not load other skills or help
pages to learn the command line.

Hand a step over only when it applies and is worth a cold start: a profile starts with an empty context and reads
the code again. No design role when the change has no user-facing surface. For a small change (one or two files,
the cause already known), do explore and plan yourself in this chat and keep the roles for build, QA and review.
Mechanical work is never handed over: formatting, a commit, a commit message or pull request body, a rerun of a
check. It takes you a minute and a profile a cold start. Note each step you kept in the run log with its reason.

1. **Brief.** Write it to a file next to the run log (`brief-<role>.md`). Start with:

   ```
   Direct request from a delivery run. No task board, no cards: do this work and reply with the result.
   Work only in <worktree>. Do not commit, push, merge, open pull requests or message anyone.
   Do not skip or weaken tests. Never print secrets or .env contents.
   A failure that also happens without this change is the baseline: note it and keep going. Never stop for a
   decision: choose within the plan's scope and say what you chose.
   The user's instructions for this work, word for word; they settle the questions they answer:
   <each instruction from now.md>
   Report findings with their evidence and the fix you suggest, never a verdict such as hold, blocked or not
   ready for release: the delivery run decides.
   Use your own skills for this kind of work where you have them.
   Keep it lean: the project's own dev server, test runner and tools first; evidence is the command and its
   result; no hash manifests, integrity receipts or copies of the tree. At your time budget, stop and reply
   with what you have and what is left.
   Every reply resends all you have read, so read little: the lines you need (search first, a whole file only
   when you need all of it), never a file you already have, independent reads and searches together in one
   reply. Test and build output goes to a file in <run dir>/logs/; read its summary and the failures.
   Load only the cpipe files named below, each as skill_view(name='cpipe:run', file_path='<file>'); never
   skill_view cpipe:run without a file_path: that is the delivery run's own procedure, not yours. Skip the
   project's general guides (architecture, standards, contributing): the plan names the rules that apply.
   Never list or poll processes. Write only the files your work needs and your reply file, never the run log,
   now.md, receipts or helper scripts around the project's own commands.
   Reply in at most 20 lines: the result, the evidence, what is left. Put anything longer in
   <run dir>/<role>-reply.md and give its path.
   ```

   A step file named below is loaded with `skill_view(name='cpipe:run', file_path='steps/<name>.md')`. What is
   already in a file (`plan.md`, the explore map, the design note) is given by its path, not copied; never the
   whole run log. Roles that
   start together (QA, review, security) get their briefs written in one reply. Write it in plain sentences, with
   everything the role needs now: a profile cannot receive anything later, so start it only once its inputs are
   ready, and never tell it to wait for a result or that something will be provided.

   Then the role's part:
   - **explore:** the request and the worktree. Ask for a read-only map: the instructions that apply, the files
     to change, the flow map as `steps/plan.md` step 2 describes (entrances, exits and their consumers, guards,
     the behavior-today table), helpers to reuse, the check commands. Paths and line numbers, no edits.
   - **design:** the request, the explore map, and the steps of `steps/design.md`. Ask for the design note.
   - **plan:** the request, the explore map, the baseline and the design note. Ask for a plan in the format of
     `steps/plan.md` step 5.
   - **build:** `plan.md`, the playbook and `steps/build.md`. Ask it to reply per plan step: done or corrected
     (what and why), and the narrow tests it ran with their results; you write those rows in the run log. It
     does not read or write the run log, and does not run the full suite or the production build: verify does.
     Every file it adds or grows stays within the file size budget in this round, not in a later cleanup.
   - **qa:** the acceptance criteria, the design note, how to start the app, and verify's broader suite result
     for this tree (start QA once verify has it). Ask it to follow `steps/qa.md` without editing the repository
     and reply with each item's result and evidence.
   - **review / security:** the reviewer brief from `steps/review.md`.

   QA, review and security never edit the repository. Before starting them, save `git status --porcelain` and
   `git diff` beside the run log, and compare after: a file they changed or added means the role failed. Undo only
   their edits, back to the saved state, and follow "When a role fails".

2. **Run** it in the worktree, directly, or as a card when the run is on the board (below):
   `hermes -p <profile> chat -Q --query-file <brief> --in <worktree> --run-budget <seconds> [--reasoning <effort>]`
   `--reasoning` only when a budget is set (Budget, above), on every run and resume. Budgets in seconds: explore, design and plan 600; review, security and qa 900; build 2400. Use your terminal's
   background mode with a completion notification when it has one; otherwise a timeout above the budget. Write
   its process id and output file in the run log and `now.md`, and once it ends, the session id it prints last
   (`session_id: …`): the profile keeps running when this chat restarts, a continue reads its result instead of
   starting it again, and every follow-up resumes that session. Then wait the way `cpipe:run` says (Waiting ends
   the turn). The budget is a hard limit: if the profile has not replied two minutes after it, stop the process
   yourself and treat the role as failed. Never wait longer.

   The result is what the process printed, or `<role>-reply.md` when it points there. Never search the profile's
   session history or transcripts for it: when neither holds a result, the role failed.

3. **Follow-ups.** Each role has one session per delivery. Every later piece of work for that role (a fix
   round, a correction, the cleanup, a QA re-run, a delta review, a fix review) resumes it:
   `hermes -p <profile> chat -Q --resume <session id> --query-file <brief> --in <worktree> --run-budget <seconds> [--reasoning <effort>]`
   A new session reads the plan and the code again from nothing, tens of replies; start one only when the resume
   fails, and note why in the run log. The follow-up brief holds only what is new; the session already has the
   header and the plan. A fix round's brief lists each finding itself: the file and line, what is wrong, the
   expected behavior and the check that shows it. Never the paths of review, QA or probe files for the builder to
   read.

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
   or `archived` (a script file if the approval policy blocks the loop). Keep working on anything that does not
   depend on it, then end the turn (`cpipe:run`, Waiting ends the turn).
4. **Read** the result from `latest_summary` and the comments. It is a claim, as below.

The card failed if it ends blocked or with no usable result, is still running two minutes past its budget, or is
still not claimed after 10 minutes
(no dispatcher is running). Archive it (`hermes kanban archive <card id>`), note why, and follow "When a role
fails". Only ever touch cards this run created.

## What comes back is a claim

- After explore: read only the lines around each place it cites before you rely on it, not whole files.
- After design or plan: check the draft against the code and the baseline; you write the final version.
- After build: `git status` and the diff in the worktree, then `steps/verify.md`. Its states are the truth, not the
  role's summary. Undo anything outside the plan's scope.
- After QA: reproduce a failure yourself before acting on it.
- After review: sort the findings yourself as `steps/review.md` says.
- A verdict (approved, blocked, hold, not ready for release) decides nothing. Take the findings behind it and
  decide with the user's instructions.

## When a role fails

The profile does not exist, the command errors, it returns nothing usable, or it broke a rule in the brief: try
once more with a shorter brief, then do the step yourself in this chat. If it timed out, do not retry: do the step
yourself right away. Tell the user in one line
(`build: <profile> failed (<reason>), running it in this chat`) and note it in the run log and the report. A role
failing is never a reason to stop the run.
