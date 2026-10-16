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
   ```

   A step file named below is loaded with `skill_view(name='cpipe:run', file_path='steps/<name>.md')`.

   Then the role's part:
   - **explore:** the request and the worktree. Ask for a read-only map: the instructions that apply, the files
     to change and their callers, helpers to reuse, the check commands. Paths and line numbers, no edits.
   - **design:** the request, the explore map, and the steps of `steps/design.md`. Ask for the design note.
   - **plan:** the request, the explore map, the baseline and the design note. Ask for a plan in the format of
     `steps/plan.md` step 5.
   - **build:** the plan, the playbook, the run log path. Ask it to follow `steps/build.md` and reply with
     what changed and which tests it ran with their results.
   - **qa:** the acceptance criteria, the design note, how to start the app. Ask it to follow `steps/qa.md`
     without editing code and reply with each item's result and evidence.
   - **review / security:** the reviewer brief from `steps/review.md`.

2. **Run** it in the worktree:
   `hermes -p <profile> chat -Q --query-file <brief> --in <worktree> --run-budget <seconds>`
   Budgets: explore, design and plan 900; review and security 1200; qa 1800; build 3600. Use your terminal's
   background mode with a completion notification when it has one; otherwise a timeout above the budget. Keep the
   session id it prints.

3. **Follow-ups** to the same role (a fix round, a delta review) resume its session:
   `hermes -p <profile> chat -Q --resume <session id> --query-file <brief>`

## What comes back is a claim

- After explore: open the files it cites before you rely on them.
- After design or plan: check the draft against the code and the baseline; you write the final version.
- After build: `git status` and the diff in the worktree, then `steps/verify.md`. Its states are the truth, not the
  role's summary. Undo anything outside the plan's scope.
- After QA: reproduce a failure yourself before acting on it.
- After review: sort the findings yourself as `steps/review.md` says.

## When a role fails

The profile does not exist, the command errors, it times out, it returns nothing usable, or it broke a rule in
the brief: try once more with a shorter brief, then do the step yourself in this chat. Tell the user in one line
(`build: <profile> failed (<reason>), running it in this chat`) and note it in the run log and the report. A role
failing is never a reason to stop the run.
