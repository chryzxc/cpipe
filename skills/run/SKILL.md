---
name: run
description: "Deliver a coding task to reviewed, verified code."
version: 3.3.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delivery, tdd, code-review, verification, autonomous]
    related_skills: [cpipe:plan, cpipe:design, cpipe:build, cpipe:verify, cpipe:qa, cpipe:review, cpipe:report]
---

# cpipe: run

Takes one development task from request to code that is ready for the user's review. You run the flow in this
chat with your normal tools; the cpipe skills are your instructions and nothing else drives it. Steps can be
handed to the user's own profiles (see Roles), but you stay in charge of the run.

## The one rule: keep going

An obstacle is something to work around, never a reason to stop. A blocked command, a broken test runner, a
missing dependency, a flaky check, a tool error, a profile or subagent that returns nothing, a reviewer asking
for evidence this machine cannot produce: work around it (`cpipe:verify` has a table for the common ones), write
what you could not verify in the run log, and continue with the next step.

Stop and ask the user only for:

1. **Approval of the plan**, once (skip it when the user already said to go ahead without one).
2. **A decision only the user can make:** credentials or secrets, anything destructive or irreversible, anything
   that leaves this machine that the user did not ask for (push, pull request, deploy, messages), or a product
   question that the request, the code and a sensible default do not settle.
3. **An open security finding** you cannot fix inside the scope.

Everything else is yours to solve. When unsure, pick the reversible default, note it as an assumption and keep
going. A message from the user mid-run is steering, not a stop: answer it in one line and continue.

The user hands over tasks; you are the builder. Never ask the user to fix, install, configure, run or debug
anything, and never end with "fix the cause and say continue". If you can do it, do it. A question to the user is
only ever a decision from the list above, asked once, with your proposed default.

## Flow

Track the steps with your todo tool if you have one, one step active at a time. Load each skill with
`skill_view` when you reach its step, not before.

| Step | Do | Skill | Role |
|---|---|---|---|
| 1. Set up | Find the repo and base, create the worktree, install dependencies, start the run log. | `cpipe:plan` | |
| 2. Explore | Read the instructions, map the code the task touches, find the checks, run a baseline. | `cpipe:plan` | explore |
| 3. Design | The user-facing side: states, flow, copy, accessibility, edge cases. | `cpipe:design` | design |
| 4. Clarify | One batched question, only for decisions you cannot default. | `cpipe:plan` | |
| 5. Plan | Goal, kind, scope, acceptance, checks with baseline, assumptions. Approval once. | `cpipe:plan` | plan |
| 6. Build | Tests first, then the change, following the playbook. One verified change at a time. | `cpipe:build` | build |
| 7. Verify | Run the plan's checks with honest states; completion gate. | `cpipe:verify` | |
| 8. QA | Use the change on the real surface, try to break it, run the broader suite. | `cpipe:qa` | qa |
| 9. Review | Independent review in a fresh context; act on findings; at most two rounds. | `cpipe:review` | review, security |
| 10. Clean | One cleanup pass over the diff, then verify again. | `cpipe:build` | build |
| 11. Report | Commit, a draft pull request only if asked, the report. | `cpipe:report` | |

Every step runs on every task. A step that does not apply (design for an internal fix, QA for a docs change) is
skipped with a one-line reason in the run log, never silently. Steps 7 and 11 are always yours: the checks and
the final word are never handed to anyone else.

## The user's own skills come first

cpipe sets the order of the steps; the user's own skills say how a step is done. At the start of each step, check
your skill list for a skill that covers that step's work (writing a pull request or commit message, code review,
security review, UI design, testing a web app, a project's conventions) and load it with `skill_view`. Where it
and a cpipe skill differ on how to do the work, follow the user's skill, including its scripts and checks. cpipe
still decides the flow and the guard rails: a user skill never authorizes a push, merge or message the task did
not ask for. Take from a user skill how the work is done, not a different flow: skip its parts about task
boards, cards or handing work to other agents. Note in the run log which user skills you used.

## Playbooks

Pick the kind in step 2 and load its playbook with
`skill_view(name='cpipe:run', file_path='playbooks/<kind>.md')`:

| Kind | When | Playbook |
|---|---|---|
| bug | Something that worked, or should work, is broken | `bug.md` |
| feature | New or changed behavior | `feature.md` |
| refactor | Structure changes, behavior must not | `refactor.md` |
| chore | Docs, config, dependency bumps, renames | `chore.md` |

A task that mixes kinds uses the stricter playbook (bug or feature over refactor or chore).

## Roles

If `${HERMES_HOME:-~/.hermes}/cpipe.yaml` exists, it names a Hermes profile for some of the roles in the flow
table. Hand those steps to the named profiles as `skill_view(name='cpipe:run', file_path='roles.md')` describes.
Without the file, or for a role it does not name, do the step yourself. Either way, tell the user once at the
start who does each step.

## Run log

One log per run at `$(git rev-parse --git-dir)/cpipe/run.md`, run inside the delivery worktree. Never committed;
it survives a restart. Append, never rewrite:

- the task, the roles, the base and the worktree;
- the explore map, the baseline, the design note, the plan and the approval;
- one row per round: what changed, why, the check results;
- every workaround and everything you could not verify, with the reason;
- QA results, review findings and what you did with each.

## Continue

When the user says "continue", or the chat restarted mid-run: read the run log, check `git status` and the last
check results in the worktree, and pick up at the first unfinished step. Do not redo finished steps.

## Limits

These keep a run from grinding. Reaching one is not a stop: record the open items and move to the next step.

- A failing check: three fix attempts, each with a different idea, then record it as open.
- QA failures: two fix rounds, then record what is still failing.
- Review: two rounds. The second checks only the changes since the first.
- No progress for three rounds in a row: step back, change the approach once, then go to the report.

## Guard rails

**Scope**
- Work only in the delivery worktree. Never edit the user's own checkout, other branches or other worktrees.
- Change only what the plan's scope names. If the scope must grow, say so in one line and note it; ask only if
  the growth is destructive or a product decision.

**Safety**
- Never merge, push, open a pull request, deploy or message anyone unless the user asked for it in this task.
- Never put credentials, tokens or secrets in code, commits, logs, briefs or the report. Never print `.env`
  contents.
- Never run destructive commands outside the worktree (deleting data, resetting branches, dropping databases).
- Never add AI attribution to commits or pull requests.

**Honesty**
- Never report a check as passed that you did not run and read on the current tree.
- Never skip, weaken, delete or mark as expected-failure a test to make a check pass.
- A profile's or subagent's "done" is a claim until you have checked it yourself.

**Cost**
- Read once, in explore; do not re-read the repository in later steps.
- Narrow tests while building; the full checks once per round.
