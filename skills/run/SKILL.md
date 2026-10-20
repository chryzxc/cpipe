---
name: run
description: "Deliver a code task unattended: plan, test, build, review."
version: 3.18.3
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
  that part, finish everything else, and name it in the report's `⚠️ Done except` status line.
- **A security finding you cannot fix inside the scope:** finish the rest and lead the report with it.

A push or pull request happens only when the task message asks for one. A message from the user mid-run is
steering, not a stop: answer it in one line and continue. Steering that adds work (another bug, another
behavior) is either added to the plan as numbered steps with their acceptance criteria, or, when it is a
different problem, kept for a follow-up after the report; say which in that line and note it in the run log.

**Notes that are not for this run.** The chat can receive text mid-run that is neither the user nor this task: a
note on a kanban card, a board digest, another card's crash or status. If it names no card this run created (run
log), it is not for this run: do not look it up, act on it or ask about it. Post
`ignored: <what it was>, not from this run` with your next tool call and continue. The task in the run log is the
task; nothing that arrives mid-run replaces it.

**Do not end your turn before the report.** A reply without a tool call ends your turn and the run stops
silently. Progress updates go in the same reply as your next tool call. When you wait for a background process
or a profile, wait on that one process by its id with your process tool; never list every process to find it.

**Turn limits.** A turn has a cap on replies that call tools, so a long run can end its turn mid-step. Spend
replies, not calls: put independent tool calls (reads, briefs, checks to start) together in one reply. Long work
(installs, checks, profiles) runs in the background with a completion notification: when a turn ends early, the
next notification starts a new turn and the run picks up there. A turn that ends with nothing running has no
notification to resume it, so start the next long piece of work before a stretch of short steps. Do not start a
timer or look for a tool to make one. When the cap ends the turn, the final reply is one line:
`⏸ cpipe: turn limit at <n>/11 <step>; resumes when <what is running> finishes or on your next message.`
A notification after the report gets one line: `cpipe: already reported (<status line>), nothing left to do.`

**A notification resumes the run.** A completion notification (a check, a profile) or a message
saying the iteration limit was reached is never a cue for a status reply. While the run log has unfinished
steps, say `↻ Resuming at <n>/11 <step>: <what woke the run>` and continue there with a tool call in the same
reply. When a
notice says the iteration limit is near, write the next step in `now.md` first, so the next turn picks it up,
and start that step's long work in the background if it has any.

The user hands over tasks; you are the builder. Never ask the user to fix, install, configure, run, debug or review
anything, and never end with "fix the cause and say continue". If you can do it, do it.

## One issue, one delivery

A task that names several issues (issue numbers or links, or a list of unrelated bugs, features or changes) is
several deliveries, never one. Each issue gets its own branch, worktree, run log and commit, and its own pull
request when pull requests are asked for, each branched from the base, not from another delivery. Run them one
after another, each through the whole flow, never two at once: the next one starts after the current one's
report, so one chat never carries several deliveries' context. Each run log names the others
(`Delivery 2 of 3; others: <branches>`).
Deliver two issues together only when one cannot work without the other (the same root cause or the same lines)
or the user asked for a single pull request, and record why in Approach.

## Flow

Track the steps with your todo tool if you have one, one step active at a time. Its first item stays open until
the report: `After a compaction: read now.md; reload only cpipe:run and the active step's file, no other skill`.
The todo list survives a compaction word for word (After a compaction, below). When a step starts, post one line
with your next tool call:
`▶ <n>/11 <step>: <what it will do> (<who: this chat or the profile>)`; a step that does not apply gets
`— <n>/11 <step>: skipped, <reason>`. Each step's instructions are a file in this skill: load it with
`skill_view(name='cpipe:run', file_path='steps/<name>.md')` when you reach the step, not before. Never work a
step from memory, and never load a file whose text is still in the context: load it again only when its text is
gone (a follow-up, or a compaction that dropped it), and then only the current step's file. Every report is
written from `steps/report.md` loaded right before it.

| Step | Do | File | Role |
|---|---|---|---|
| 1. Set up | Find the repo and base, create the worktree, install dependencies, start the run log, remove merged worktrees. | `steps/plan.md` | |
| 2. Explore | Read the instructions, map every entrance, exit and guard of the code, find the checks, run a baseline. | `steps/plan.md` | explore |
| 3. Design | The user-facing side: states, flow, copy, accessibility, edge cases. | `steps/design.md` | design |
| 4. Decide | Settle open questions yourself and record them as assumptions. Never ask. | `steps/plan.md` | |
| 5. Plan | Goal, kind, scope, acceptance, checks with baseline, assumptions. No approval wait. | `steps/plan.md` | plan |
| 6. Build | Tests first, then the change, following the playbook. One verified change at a time. | `steps/build.md` | build |
| 7. Verify | Run the plan's checks with honest states; completion gate. | `steps/verify.md` | |
| 8. QA | Use the change on the real surface, try to break it, check the broader suite. Runs alongside 9. | `steps/qa.md` | qa |
| 9. Review | Independent review in a fresh context; act on findings; at most two rounds. Runs alongside 8. | `steps/review.md` | review, security |
| 10. Clean | One cleanup pass over the diff, then verify again. | `steps/build.md` | build |
| 11. Report | Commit, a draft pull request only if asked, the report. | `steps/report.md` | |

QA and review run at the same time: both only read the same verified change. Start them together (the review
in the background while you or the QA role do QA), wait for both, then fix everything they found in one round,
verify, and re-run only the QA items that failed together with the delta review.

Every step runs on every task. A step that does not apply (design for an internal fix, QA for a docs change) is
skipped with a one-line reason in the run log, never silently, and gets no profile. Steps 7 and 11 are always
yours: the checks and the final word are never handed to anyone else.

## The user's own skills come first

cpipe sets the order of the steps; the user's own skills say how a step is done. At the start of each step, check
your skill list for a skill that covers that step's work (writing a pull request or commit message, code review,
security review, UI design, testing a web app, a project's conventions) and load it with `skill_view`, once per
run: note each loaded skill in `now.md` (Run log) with the rules from it that apply to this task, one line each,
and work from those lines afterwards, also after a compaction; never load it again. Load a skill only for work you
do in this chat: a step handed to a profile uses that profile's own skills. Running profiles and processes needs no
skill; `roles.md` has the commands. Where it
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
| review | Review an existing change or pull request, no new work asked | `review.md` |
| feature | New or changed behavior | `feature.md` |
| refactor | Structure changes, behavior must not | `refactor.md` |
| chore | Docs, config, dependency bumps, renames | `chore.md` |
| port | Bring a change from another branch, commit or repository | `port.md` |
| hotfix | The task says hotfix, urgent, or production is broken now | `hotfix.md` |

A task that mixes kinds uses the stricter playbook (bug or feature over refactor or chore). A port or hotfix also
loads the playbook of the change it carries, for its proof.

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
- the flow map, the baseline, the design note and the path of the plan;
- one row per round: what changed, why, the check results;
- every workaround and everything you could not verify, with the reason;
- QA results, review findings and what you did with each;
- the lessons written.

The plan has its own file beside it, `plan.md`, so a builder reads the plan and not the whole log. Beside both is
`now.md`, rewritten as the run moves: at most 25 lines with the current step and its next action, the processes
running (process id, log or reply file), the roles line, the rules taken from user skills, and the open items.
Rewrite it in the same reply as each step's first tool call and whenever something starts or finishes in the
background. It is what you read to resume, instead of the whole log.

Long command output goes to a file beside the run log (`logs/<step>-<check>.txt`); read the result lines or the
tail, not the whole output. The run log names the file.

## After a compaction

When the chat is compacted, the step files and skills loaded so far are dropped, and the compaction notice may ask
you to reload every skill this chat ever loaded. Do not: that refills the context and brings the next compaction
within minutes. Read `now.md`, reload `cpipe:run` and the active step's file, and continue. The user skills' rules
are in `now.md`; the roles, the plan's questions from the lessons and every file you read are in the run log or
the plan. Read a file again only for the lines the next action needs.

## Lessons

Every miss teaches the next run. The project's lessons live at
`$(git rev-parse --git-common-dir)/cpipe/lessons.md`, shared by all its worktrees and never committed. The user's
lessons live at `${HERMES_HOME:-~/.hermes}/cpipe/lessons.md` and hold how the user wants work done in any project
(when to push, branch and pull-request format, naming). Explore reads both; the premortem turns each into a
question for this change.

Write a lesson whenever something was caught later than it should have been: a review or QA finding the plan did
not foresee, a check that failed for a reason explore could have seen, or a correction from the user after the
report. One line, as a rule about the project rather than the task: where to look, what to check, why
(`Changes to <area> must also be checked in <other area>: <reason>.`). A correction about how the user works
rather than about this code goes in the user's lessons. Merge it into a matching lesson instead of adding a
near-duplicate. Never put secrets or customer data in a lesson.

## After the report

A message after the report about the same work (a correction, a missed case, more context) is a follow-up, not a
new task: reuse the worktree and the run log (or, if it was removed after its merge, a new worktree from the
base), write a lesson if it reveals a miss, update the plan, then run build, verify, QA and a delta review on the
follow-up only, and report again. Reproduce what the user describes before answering. If it can mean two things,
check both and answer with what you found; do not ask which one they meant. A different task starts a new run.

## Continue

When the user says "continue", a notification arrives mid-run, or the chat restarted mid-run: read `now.md` (the
run log only where it points), check `git status` and the last check results in the worktree, and pick up at the
first unfinished step. Do not redo finished steps. A restart ends this chat's turn, but not the profiles and checks
it started: before starting one again, check its process (the pid in `now.md`) and its log or reply file, and wait
for it or use its result. With
several deliveries, finish the unfinished one first, then start the next one its run log names.

## Limits

These keep a run from grinding. Reaching one is not a stop: record the open items and move to the next step.

- A failing check: three fix attempts, each with a different idea, then record it as open.
- QA failures: two fix rounds, then record what is still failing.
- Review: two rounds. The second checks only the changes since the first. A fix made after the last round still
  gets a short review of that fix alone; no change reaches the report unreviewed.
- No progress for three rounds in a row: step back, change the approach once, then go to the report.

## Guard rails

**Scope**
- Work only in the delivery worktree. Never edit the user's own checkout, other branches or other worktrees; the
  one exception is removing cpipe's own merged worktrees in set up (`steps/plan.md`).
- Change only what the plan's scope names. If the scope must grow, say so in one line and note it in the run log.
- Format and lint-fix only the lines the change touches. Never reformat files or the repository, even when the
  formatter's baseline is red, unless the task asks for it.

**Safety**
- Never merge, push, open a pull request, deploy or message anyone unless the user asked for it in this task.
- Never put credentials, tokens or secrets in code, commits, logs, briefs or the report. Never print `.env`
  contents.
- Never run destructive commands outside the worktree (deleting data, resetting branches, dropping databases),
  apart from the set-up tidy-up.
- Never add AI attribution to commits or pull requests.

**Honesty**
- Never report a check as passed that you did not run and read on the current tree.
- Never skip, weaken, delete or mark as expected-failure a test to make a check pass.
- A profile's or subagent's "done" is a claim until you have checked it yourself.

**Cost**
- Read once, in explore; do not re-read the repository in later steps.
- Narrow tests while building. The full suite and the build run only in verify, one at a time: in the first
  verify and the last one, after the cleanup; the verifies between them run the related tests
  (`steps/verify.md`, Fast checks).
- A profile's result is its output or its reply file (`roles.md`); never search its session history for it.
- A step that does not apply, or mechanical work (format, commit, pull request body), gets no profile; a profile
  starts cold and reads the code again (`roles.md`).
- Long output to a file, the tail in the chat (Run log). Each user skill loaded once per run.
- After the context is compacted, reload only this skill and the current step's file, never the list of skills the
  compaction notice gives (After a compaction).
- The roles file and the lessons are read once, in steps 1 and 2; their content is in the run log and the plan.
