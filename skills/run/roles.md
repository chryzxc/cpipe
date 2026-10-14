# cpipe: roles

Optional. Without a roles file, you run every step yourself in this chat.

## The roles file

`${HERMES_HOME:-~/.hermes}/cpipe.yaml`, written by the user:

```yaml
roles:
  plan: <profile>       # drafts the plan (step 3)
  build: <profile>      # writes the tests and the code (steps 5 and 8)
  review: <profile>     # the reviewer (step 7)
  security: <profile>   # second reviewer when the change touches security-sensitive code
```

Every key is optional. A role that is missing or empty is done by you in this chat, as usual. Read the file
once at step 1, record the roles in the run log, and tell the user in one line who does each step, for example:
`Roles: plan → archon, build → forge, review → this chat (no profile set), security → this chat (no profile set)`.
Without a roles file, say `Roles: no cpipe.yaml, every step runs in this chat`. This is a notice, not a
question: keep going.

## Handing a step to a role

You stay in charge of the run: you run the flow, the checks, the run log and every decision. A role only does
the work of its step and reports back.

1. Write the brief to a file in the run log's directory (`brief-<step>.md`). It starts with: "Direct request
   from a delivery run, no task board: do this work and reply with the result." Then:
   - **plan:** the request, the repository, what you found in step 1 (instructions, code, checks, baseline).
     Ask for the plan in the format of `cpipe:plan`.
   - **build:** the approved plan, the worktree path, the playbook, the run log path. Ask it to build in that
     worktree only, commit nothing, and reply with what changed and which tests it ran.
   - **review / security:** the reviewer brief from `cpipe:review`.
2. Run it in the worktree:
   `hermes -p <profile> chat -Q --query-file <brief> --in <worktree> --run-budget <seconds>`.
   Use your terminal's background mode with a completion notification when it has one; otherwise a long
   timeout. Keep the session id it prints.
3. Follow-ups to the same role (a fix round, a delta review) resume its session:
   `hermes -p <profile> chat -Q --resume <session id> --query-file <brief>`.

## What comes back is a claim

Read the result against the worktree yourself. After a build, run `cpipe:verify`: its states are the truth, not
the role's summary. After a review, sort the findings yourself as `cpipe:review` says.

## When a role fails

The profile does not exist, the command errors, it times out, or it returns nothing usable: try once more with
a shorter brief, then do the step yourself in this chat. Tell the user in one line when it happens
(`build: forge failed (<reason>), running it in this chat`) and note it in the run log and the report. A role
failing is never a reason to stop the run.
