---
name: run
description: "Deliver a coding task to reviewed, verified code."
version: 3.0.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delivery, tdd, code-review, verification, autonomous]
    related_skills: [cpipe:plan, cpipe:build, cpipe:verify, cpipe:review, cpipe:report]
---

# cpipe: run

Takes one development task from request to code that is ready for the user's review: clarify once, plan, one
approval, tests first, build, verify with the project's own checks, independent review, a cleanup pass, and an
honest report. You run every step yourself in this chat, with your normal tools. The skills are your
instructions; nothing else drives the flow.

## The one rule: keep going

An obstacle is something to work around, never a reason to stop. A blocked command, a broken test runner, a
missing dependency, a flaky check, a tool error, a subagent that returns nothing, a reviewer asking for evidence
this machine cannot produce: work around it (the table in `cpipe:verify` covers the common ones), write what you
could not verify in the run log, and continue with the next step.

Stop and ask the user only for:

1. **Approval of the plan**, once (skip it when the user already said to go ahead without one).
2. **A decision only the user can make:** credentials or secrets, anything destructive or irreversible, anything
   that leaves this machine that the user did not ask for (push, pull request, deploy, messages), or a product
   question that the request, the code and a sensible default do not settle.
3. **An open security finding** you cannot fix inside the scope.

Everything else is yours to solve. When you are unsure, pick the reversible default, note it as an assumption,
and keep going. A message from the user mid-run is steering, not a stop: answer it in one line and continue.

The user hands over tasks; you are the builder. Never ask the user to fix, install, configure, run or debug
anything, and never end with "fix the cause and say continue". If you can do it, do it. A question to the user is
only ever a decision from the list above, asked once with your proposed default.

## Flow

Track the steps with your todo tool if you have one, one step active at a time.

| Step | Do | Skill |
|---|---|---|
| 1. Understand | Find the repo, read its instructions, read only the code the task touches, find the checks, run a baseline. | `cpipe:plan` |
| 2. Clarify | One batched question, only for decisions you cannot default. Everything else becomes an assumption. | `cpipe:plan` |
| 3. Plan | Short plan: goal, kind, scope, acceptance, checks with baseline, assumptions. Ask for approval once. | `cpipe:plan` |
| 4. Worktree | A new branch and worktree from the base; install the project's dependencies there. | `cpipe:build` |
| 5. Build | Follow the playbook for the task kind (below). One verified change at a time. | `cpipe:build` |
| 6. Verify | Run the plan's checks, record an honest state for each, run the completion gate. | `cpipe:verify` |
| 7. Review | Independent review in a fresh context; act on findings; at most two rounds. | `cpipe:review` |
| 8. Clean | One cleanup pass over your own diff, then verify again. | `cpipe:build` |
| 9. Report | Commit, open a draft pull request only if asked, and report. | `cpipe:report` |

Load each skill with `skill_view` when you reach its step, not before.

## Playbooks

Pick one from the task in step 1 and load it with
`skill_view(name='cpipe:run', file_path='playbooks/<kind>.md')`:

| Kind | When | Playbook |
|---|---|---|
| bug | Something that worked, or should work, is broken | `bug.md` |
| feature | New or changed behavior | `feature.md` |
| refactor | Structure changes, behavior must not | `refactor.md` |
| chore | Docs, config, dependency bumps, renames | `chore.md` |

A task that mixes kinds uses the stricter playbook (bug or feature over refactor or chore).

## Roles (optional)

If `${HERMES_HOME:-~/.hermes}/cpipe.yaml` exists, it names a Hermes profile for some roles (plan, build,
review, security). Hand those steps to the named profiles as described in
`skill_view(name='cpipe:run', file_path='roles.md')`; you still run the flow and verify their work. Without the
file, or for a role it does not name, do the step yourself. Either way, tell the user once at the start who
does each step.

## Run log

Keep one log per run at `$(git rev-parse --git-dir)/cpipe/run.md` in the delivery worktree. It is never
committed and survives a restart. Append, never rewrite:

- the plan (or a link to it) and the approval;
- one row per round: what changed, why, the check results;
- every workaround and everything you could not verify, with the reason;
- review findings and what you did with each.

## Continue

When the user says "continue", or the chat restarted mid-run: read the run log, check `git status` and the last
check results in the worktree, and pick up at the first unfinished step. Do not redo finished steps.

## Limits

These keep a run from grinding. Reaching one is not a stop: hand over what you have, with the open items listed.

- A failing check: three fix attempts with a different idea each, then record it as open and move on.
- Review: two rounds. The second checks only the changes since the first.
- No progress for three rounds in a row: step back, change the approach once, then hand over.

## Never

- Merge, push, open a pull request, deploy or message anyone unless the user asked for it in this task.
- Skip, weaken, delete or mark as expected-failure a test to make a check pass.
- Touch the user's own checkout, other branches, or files outside the plan's scope (say so if scope must grow).
- Put credentials, tokens or secrets in code, commits, logs or the report.
- Report a check as passed that you did not run and read.
- Add AI attribution to commits or pull requests.
