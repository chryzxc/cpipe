---
name: run
description: "Deliver a coding task unattended, from request to report."
version: 3.9.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delivery, tdd, code-review, verification, autonomous]
---

# cpipe: run

Takes one development task from request to code that is ready for the user's review. You run the flow in this
chat with your normal tools; this skill and its files are your instructions and nothing else drives it. Steps can be
handed to the user's own profiles (see Roles), but you stay in charge of the run.

## The one rule: keep going

An obstacle is something to work around, never a reason to stop. A blocked command, a broken test runner, a
missing dependency, a flaky check, a tool error, a profile or subagent that returns nothing, a reviewer asking
for evidence this machine cannot produce: work around it (`steps/verify.md` has a table for the common ones), write
what you could not verify in the run log, and continue with the next step.

## Unattended: the task is the last message

The user sends the task and waits for the report. Never ask a question and never wait for an answer: no plan
approval, no clarifying questions, no "should I". Never call a tool that waits for the user (a clarify or
question tool, a command that needs the user's approval): it halts the run until they answer. Use another way,
or skip that part and report it. Every choice is yours:

- **Open questions** (which base, which of two behaviors, a product detail): decide from the request, the code,
  the project's history and the conversation; pick the most likely and reversible answer, record it as an
  assumption, and keep going.
- **Things only the user may do** (credentials or secrets, anything destructive or irreversible, anything that
  leaves this machine that the task did not ask for: push, pull request, deploy, messages): never do them. Skip
  that part, finish everything else, and list it under "Needs your decision" in the report.
- **A security finding you cannot fix inside the scope:** finish the rest and lead the report with it.

A push or pull request happens only when the task message asks for one. A message from the user mid-run is
steering, not a stop: answer it in one line and continue.

**Do not end your turn before the report.** A reply without a tool call ends your turn and the run stops
silently. Progress updates go in the same reply as your next tool call. When you wait for a background process
or a profile, wait on it with your process tool; if your turn does end, the completion notification resumes the
run at the first unfinished step.

The user hands over tasks; you are the builder. Never ask the user to fix, install, configure, run or debug
anything, and never end with "fix the cause and say continue". If you can do it, do it.

## Flow

Track the steps with your todo tool if you have one, one step active at a time. Each step's instructions are a
file in this skill: load it with `skill_view(name='cpipe:run', file_path='steps/<name>.md')` when you reach the
step, not before.

| Step | Do | File | Role |
|---|---|---|---|
| 1. Set up | Find the repo and base, create the worktree, install dependencies, start the run log. | `steps/plan.md` | |
| 2. Explore | Read the instructions, map every entrance, exit and guard of the code, find the checks, run a baseline. | `steps/plan.md` | explore |
| 3. Design | The user-facing side: states, flow, copy, accessibility, edge cases. | `steps/design.md` | design |
| 4. Decide | Settle open questions yourself and record them as assumptions. Never ask. | `steps/plan.md` | |
| 5. Plan | Goal, kind, scope, acceptance, checks with baseline, assumptions. No approval wait. | `steps/plan.md` | plan |
| 6. Build | Tests first, then the change, following the playbook. One verified change at a time. | `steps/build.md` | build |
| 7. Verify | Run the plan's checks with honest states; completion gate. | `steps/verify.md` | |
| 8. QA | Use the change on the real surface, try to break it, run the broader suite. | `steps/qa.md` | qa |
| 9. Review | Independent review in a fresh context; act on findings; at most two rounds. | `steps/review.md` | review, security |
| 10. Clean | One cleanup pass over the diff, then verify again. | `steps/build.md` | build |
| 11. Report | Commit, a draft pull request only if asked, the report. | `steps/report.md` | |

Every step runs on every task. A step that does not apply (design for an internal fix, QA for a docs change) is
skipped with a one-line reason in the run log, never silently. Steps 7 and 11 are always yours: the checks and
the final word are never handed to anyone else.

## The user's own skills come first

cpipe sets the order of the steps; the user's own skills say how a step is done. At the start of each step, check
your skill list for a skill that covers that step's work (writing a pull request or commit message, code review,
security review, UI design, testing a web app, a project's conventions) and load it with `skill_view`. Where it
and a cpipe step differ on how to do the work, follow the user's skill, including its scripts and checks. cpipe
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
start who does each step. When the task message starts with `--kanban`, the named profiles get their steps as
cards on the user's kanban board (`roles.md`, On the board); without a roles file there is nothing to hand over,
so say that in the roles line and continue.

## Run log

One log per run at `$(git rev-parse --git-dir)/cpipe/run.md`, run inside the delivery worktree. Never committed;
it survives a restart. Append, never rewrite:

- the task, the roles, the base and the worktree, and every card id when on the board;
- the flow map, the baseline, the design note, the plan and its assumptions;
- one row per round: what changed, why, the check results;
- every workaround and everything you could not verify, with the reason;
- QA results, review findings and what you did with each;
- the lessons written.

## Lessons

Every miss teaches the next run. The project's lessons live at
`$(git rev-parse --git-common-dir)/cpipe/lessons.md`, shared by all its worktrees and never committed. Explore
reads them; the premortem turns each into a question for this change.

Write a lesson whenever something was caught later than it should have been: a review or QA finding the plan did
not foresee, a check that failed for a reason explore could have seen, or a correction from the user after the
report. One line, as a rule about the project rather than the task: where to look, what to check, why
(`Changes to <area> must also be checked in <other area>: <reason>.`). Merge it into a matching lesson instead of
adding a near-duplicate. Never put secrets or customer data in a lesson.

## After the report

A message after the report about the same work (a correction, a missed case, more context) is a follow-up, not a
new task: reuse the worktree and the run log, write a lesson if it reveals a miss, update the plan, then run
build, verify, QA and a delta review on the follow-up only, and report again. A different task starts a new run.

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
