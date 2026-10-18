# cpipe: roles

Optional. Without a roles file, you run every step yourself in this chat.

## The roles file

`${HERMES_HOME:-~/.hermes}/cpipe.yaml`, written by the user:

```yaml
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

## Handing a step to a role

You stay in charge: the flow, the checks, the run log and every decision are yours. A role does the work of its
step and reports back.

1. **Brief.** Write it to a file next to the run log (`brief-<role>.md`). Start with:

   ```
   Direct request from a delivery run. No task board, no cards: do this work and reply with the result.
   Work only in <worktree>. Do not commit, push, merge, open pull requests or message anyone.
   Do not skip or weaken tests. Never print secrets or .env contents.
   Use your own skills for this kind of work where you have them.
   Keep it lean: the project's own dev server, test runner and tools first; evidence is the command and its
   result; no hash manifests, integrity receipts or copies of the tree. At your time budget, stop and reply
   with what you have and what is left.
   ```

   A step file named below is loaded with `skill_view(name='cpipe:run', file_path='steps/<name>.md')`.

   Then the role's part:
   - **explore:** the request and the worktree. Ask for a read-only map: the instructions that apply, the files
     to change, the flow map as `steps/plan.md` step 2 describes (entrances, exits and their consumers, guards,
     behavior to keep), helpers to reuse, the check commands. Paths and line numbers, no edits.
   - **design:** the request, the explore map, and the steps of `steps/design.md`. Ask for the design note.
   - **plan:** the request, the explore map, the baseline and the design note. Ask for a plan in the format of
     `steps/plan.md` step 5.
   - **build:** the plan, the playbook, the run log path. Ask it to follow `steps/build.md` and reply per
     plan step: done or corrected (what and why), and the tests it ran with their results.
   - **qa:** the acceptance criteria, the design note, how to start the app. Ask it to follow `steps/qa.md`
     without editing code and reply with each item's result and evidence.
   - **review / security:** the reviewer brief from `steps/review.md`.

2. **Run** it in the worktree, directly, or as a card when the run is on the board (below):
   `hermes -p <profile> chat -Q --query-file <brief> --in <worktree> --run-budget <seconds>`
   Budgets in seconds: explore, design and plan 600; review, security and qa 900; build 2400. Use your terminal's
   background mode with a completion notification when it has one; otherwise a timeout above the budget. Keep the
   session id it prints. The budget is a hard limit: if the profile has not replied two minutes after it, stop the
   process yourself and treat the role as failed. Never wait longer.

3. **Follow-ups** to the same role (a fix round, a delta review) resume its session:
   `hermes -p <profile> chat -Q --resume <session id> --query-file <brief>`

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
3. **Wait** in the background with your process tool: poll `hermes kanban show <card id> --json` every 30 seconds
   until the status is `done`, `blocked`, `review` or `archived`. Keep working on anything that does not depend
   on it.
4. **Read** the result from `latest_summary` and the comments. It is a claim, as below.

The card failed if it ends blocked or with no usable result, is still running two minutes past its budget, or is
still not claimed after 10 minutes
(no dispatcher is running). Archive it (`hermes kanban archive <card id>`), note why, and follow "When a role
fails". Only ever touch cards this run created.

## What comes back is a claim

- After explore: open the files it cites before you rely on them.
- After design or plan: check the draft against the code and the baseline; you write the final version.
- After build: `git status` and the diff in the worktree, then `steps/verify.md`. Its states are the truth, not the
  role's summary. Undo anything outside the plan's scope.
- After QA: reproduce a failure yourself before acting on it.
- After review: sort the findings yourself as `steps/review.md` says.

## When a role fails

The profile does not exist, the command errors, it returns nothing usable, or it broke a rule in the brief: try
once more with a shorter brief, then do the step yourself in this chat. If it timed out, do not retry: do the step
yourself right away. Tell the user in one line
(`build: <profile> failed (<reason>), running it in this chat`) and note it in the run log and the report. A role
failing is never a reason to stop the run.
