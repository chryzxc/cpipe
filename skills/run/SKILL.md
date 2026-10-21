---
name: run
description: "Deliver a code task unattended: plan, test, build, review."
version: 3.26.1
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

## The user's instructions are the contract

Every instruction the user gives about this work, in the task or in any later message, holds until the user
changes it. That covers how to work (in parallel, which base, draft pull requests), what the change must respect
(keep the client's behavior, follow an existing rule) and how to finish (push, open a pull request, merge when a
condition holds). Write each one word for word in the run log and under `Instructions:` in `now.md` the moment it
arrives. Check every plan, decision, fix and report against them. Where an instruction differs from a default
in these files, the instruction wins. Only the Safety guard rails still apply, and an instruction to push, open a
pull request or merge gives the permission they ask for.

An instruction that settles a question is its answer. When a finding is one an instruction decides, fix it the
way the instruction says, for example a change that rejects what the user said must keep working. Never hold it
for a decision, a policy or an approval the user already gave. The run is finished when every instruction is
met. An instruction stays unmet only for a reason the report names: a limit reached after different fixes, or
something this machine cannot do.

## Unattended: the task is the last message

The user sends the task and waits for the report. Never ask a question and never wait for an answer: no plan
approval, no clarifying questions, no "should I". Never call a tool that waits for the user (a clarify or
question tool, a command that needs the user's approval): it halts the run until they answer. Use another way,
or skip that part and report it. Every choice is yours:

- **Open questions** (which base, which of two behaviors, a product detail, how old data or another role should
  be treated): decide from the user's instructions first, then the request, the code, the project's history and
  the conversation. Pick the most likely and reversible answer, record it as an assumption, and keep going.
- **Things only the user may do**, unless the user asked for them: credentials or secrets, anything destructive,
  and anything that leaves this machine (push, pull request, merge, deploy, messages). Never do them. Skip that
  part, finish everything else, and name it in the report's `⚠️ Done except` status line. How the code should
  behave is never one of them: that is an open question.
- **A security problem in code this change does not touch:** finish the rest and lead the report with it. A
  problem this change causes is fixed in this change.

A push, pull request or merge happens only when the user asks for one. A message from the user mid-run is
steering, not a stop: add its instructions to `now.md`, answer it in one line and continue. A question about the
run ("is it done?") gets its one-line answer in the same reply as the next tool call while any instruction is
unmet. Steering that adds work (another bug, another
behavior) is either added to the plan as numbered steps with their acceptance criteria, or, when it is a
different problem, kept for a follow-up after the report; say which in that line and note it in the run log.

**Notes that are not for this run.** The chat can receive text mid-run that is neither the user nor this task: a
note on a kanban card, a board digest, another card's crash or status. If it names no card this run created (run
log), it is not for this run: do not look it up, act on it or ask about it. Post
`ignored: <what it was>, not from this run` with your next tool call and continue. The task in the run log is the
task; nothing that arrives mid-run replaces it.

**Waiting ends the turn.** A reply without a tool call ends your turn. Every reply resends the whole chat, so a wait
costs a full reply each time you look: never poll, sleep, list processes or call the process tool's wait in a loop.
When a profile or check this run started is running with a completion notification and nothing in the run can move
until it finishes, end the turn with one line, `⏳ <n>/11 <step>: waiting on <what>, done by <clock time> at the
latest`, the time its time limit ends, so a long wait never looks like a hang; its notification starts the next
turn. With nothing running, a reply without a tool call stops the run silently, so end a turn only on such a wait,
the pause message or the report. Progress updates go in the same reply as your next tool call. Without completion
notifications, wait on that one process id with your process tool. Whatever you wait on ends on its own: every
install, check and profile runs under a time limit (`steps/setup.md`, Time limit), so a hang still ends it and its
notification still comes.

**Turn limits.** A turn has a cap on replies that call tools, so a long run can end its turn mid-step. Spend
replies, not calls: put independent tool calls (reads, briefs, checks to start) together in one reply. Long work
(installs, checks, profiles) runs in the background with a completion notification: when a turn ends early, the
next notification starts a new turn and the run picks up there. A turn that ends with nothing running has no
notification to resume it, so start the next long piece of work before a stretch of short steps. Do not start a
timer or look for a tool to make one. When the cap ends the turn (a message asks for a final summary without
tools), the final reply is this pause message and nothing else:

```
⏸ Paused by the turn limit at <n>/11 <step>, not finished: <goal>
Done: <what this turn finished, in one line>
Next: <the exact next action>, then <what is left until every instruction is met>
Decided: <each open question or unexplained result: the decision the next turn acts on, and why>
Resumes: when <what is running> finishes | on your next message (nothing is running)
```

Leave out `Decided` when nothing is open. An open item is never only "unresolved" or "cause unknown": it carries
the decision, made the way `steps/report.md` (Finish before reporting) makes it. The pause message is not the
report. A notification after the report gets one line: `cpipe: already reported (<status line>), nothing left to
do.` The report is the message written from `steps/report.md` once every instruction is met or has its reason.

**A notification resumes the run.** A completion notification (a check, a profile), or a new turn that starts
with a note that the last one hit its limit, is never a cue for a status reply. While the run log has unfinished
steps or `now.md` an unmet instruction, say `↻ Resuming at <n>/11 <step>: <what woke the run>` and continue
there with a tool call in the same reply. When a notice says how much of the turn's iteration budget is used,
write the next step in `now.md` first, so the next turn picks it up, and start that step's long work in the
background if it has any.

The user hands over tasks; you are the builder. Never ask the user to fix, install, configure, run, debug or review
anything, and never end with "fix the cause and say continue". If you can do it, do it.

## One issue, one delivery

A task that names several issues (issue numbers or links, or a list of unrelated bugs, features or changes) is
several deliveries, never one. Each issue gets its own branch, worktree, run log and commit, and its own pull
request when pull requests are asked for, each branched from the base, not from another delivery. Run them one
after another, each through the whole flow to its report before the next starts. The roles' sessions carry over
from one delivery to the next (`roles.md`, Follow-ups), so a builder reads the codebase once per run, not once
per issue. When the user asks for them in parallel, run at most two side by side, each lane with its own
sessions, and only issues in different areas of the code: one that turns out to change the same files as a
running one waits for it. A notification moves only the delivery it names: read that delivery's `now.md`, not
the others'. Each run log names the others (`Delivery 2 of 3; others: <branches>`). No combined or integration
branch unless the user asks for one: each delivery is verified, reviewed and merged on its own, and one whose
base moved is updated onto it (`steps/report.md`, Merge).
Deliver two issues together only when one cannot work without the other (the same root cause or the same lines)
or the user asked for a single pull request, and record why in Approach.

## Flow

Track the steps with your todo tool if you have one, one step active at a time. Its first item stays open until
the report: `After a compaction: read now.md; reload only cpipe:run and the active step's file, no other skill`.
The todo list survives a compaction word for word (After a compaction, below). When a step starts, post one line
with your next tool call:
`▶ <n>/11 <step>: <what it will do> (<who: this chat or the profile>)`; a step that does not apply gets
`— <n>/11 <step>: skipped, <reason>`. Each step's instructions are a file in this skill: load it with
`skill_view(name='cpipe:run', file_path='steps/<name>.md')` when you reach a step you do in this chat, not
before; a step a profile does needs only `roles.md` here, and the profile loads its own file. Never work a step
from memory, and never load a file whose text is still in the context: load it again only when its text is gone
(a follow-up, or a compaction that dropped it), and then only the current step's file. When `skill_view` answers
that the content is unchanged without returning it, read the file itself with your file tool from the plugin's
`skills/run/` directory. Every report is written from `steps/report.md` loaded right before it.

| Step | Do | File | Role |
|---|---|---|---|
| 1. Set up | Find the repo and base, create the worktree, start the run log, remove merged worktrees, find the checks, start the installs and the baseline in its own worktree. | `steps/setup.md` | |
| 2. Explore | Read the instructions, map every entrance, exit and guard of the code, reproduce a bug. | `steps/plan.md` | plan, else build, else explore |
| 3. Design | The user-facing side: states, flow, copy, accessibility, edge cases. | `steps/design.md` | design (a new or changed screen), else whoever plans |
| 4. Decide | Settle open questions and record them as assumptions. Never ask. | `steps/plan.md` | plan, else build |
| 5. Plan | Goal, kind, scope, acceptance, checks with baseline, assumptions. No approval wait. | `steps/plan.md` | plan, else build |
| 6. Build | Tests first, then the change, following the playbook; it builds before it is handed back. | `steps/build.md` | build |
| 7. Verify | Run the plan's checks with honest states; completion gate. | `steps/verify.md` | |
| 8. QA | Use the change on the real surface, try to break it, check the broader suite. Runs alongside 9. | `steps/qa.md` | qa |
| 9. Review | Independent review in a fresh context; act on findings; at most two rounds. Runs alongside 8. | `steps/review.md` | review, security |
| 10. Clean | The builder's cleanup of its own diff, then the last verify on the final tree. | `steps/build.md` | build |
| 11. Report | Commit, a draft pull request and a merge only if asked, the report. | `steps/report.md` | |

The review only reads the code, so it never waits for the full suite: start it in the background as soon as the
first verify's narrow tests, light checks and completion gate pass, then start the full suite and the build. QA
runs the change, so it starts once those finish (one heavy job at a time). Nobody edits the worktree while they
read it: wait for all of them, then fix everything in one round (new failures, findings, QA failures), verify,
and re-run only the QA items that failed together with the delta review.

When the user asks for a draft pull request on the first commit, early or first, it opens right after the first
verify passes its gate, and later rounds push to it (`steps/report.md`, Early draft); step 11 finishes it.

Every step runs on every task. A step that does not apply (design for an internal fix, QA for a docs change) is
skipped with a one-line reason in the run log, never silently, and gets no profile. Steps 7 and 11 are always
yours: the checks and the final word are never handed to anyone else. Steps one session does in one brief (a
planner's explore to plan, a builder's explore to build) share one ▶ line.

## The user's own skills come first

cpipe sets the order of the steps; the user's own skills say how a step is done. At the start of each step you do
in this chat, check your skill list for a skill that covers that step's work (writing a pull request or commit
message, code review, security review, UI design, testing a web app, a project's conventions) and load it with
`skill_view`, once per run: note each loaded skill in `now.md` (Run log) with the rules from it that apply to this
task, one line each, and work from those lines afterwards, also after a compaction; never load it again. Never
load one before the step that uses it or for a step a profile does: that profile uses its own skills, and a
skill loaded early is resent with every reply until the report. Running profiles and processes needs no skill;
`roles.md` has the commands. Where it
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
so say that in the roles line and continue. A `--budget <tier>` at the start of the task, or `budget:` in the
file, sets how hard the profiles reason (`roles.md`, Budget). A plan profile explores and plans on its own model
and the builder builds from its plan; without one, the builder does the reading and planning too. With a build
profile this chat never reads the project's code (`roles.md`, How the work is split).

## Run log

One log per run at `$(git rev-parse --git-dir)/cpipe/run.md`, run inside the delivery worktree. Never committed;
it survives a restart. Append, never rewrite:

- the task and every instruction from the user, word for word, the roles, the base and the worktree, and every
  card id when on the board;
- the baseline and the base worktree's path, and the paths of the plan (it holds the flow map) and the design note;
- one row per round: what changed, why, the check results;
- every workaround and everything you could not verify, with the reason;
- QA results, review findings and what you did with each;
- the lessons written.

The plan has its own file beside it, `plan.md`, so a builder reads the plan and not the whole log. Beside both is
`now.md`, rewritten as the run moves: at most 25 lines, plus the instructions. It holds the user's
instructions word for word, each marked met or not yet; the current step and its next action; the processes
running (process id, log or reply file); the roles line; the rules taken from user skills; and the open items.
Rewrite it in the same reply as each step's first tool call and whenever something starts or finishes in the
background. It is what you read to resume, instead of the whole log.

Long command output goes to a file beside the run log (`logs/<step>-<check>.txt`); read the result lines or the
tail, not the whole output. The run log names the file.

## After a compaction

When the chat is compacted, the step files and skills loaded so far are dropped, and the compaction notice may ask
you to reload every skill this chat ever loaded. Do not: that refills the context and brings the next compaction
within minutes. Read `now.md`, reload `cpipe:run` and the active step's file (from the plugin's directory when
`skill_view` returns no text, Flow), and continue. The user's
instructions and the user skills' rules are in `now.md`; the roles, the plan's questions from the lessons and
every file you read are in the run log or the plan. Read a file again only for the lines the next action needs.

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
first unfinished step. Do not redo finished steps. Sometimes a report left an instruction unmet, and then the
user says "continue" or repeats the instruction. That is the same task: do what is unmet, never only report again
that it is not done. A restart ends this chat's turn, but not the profiles and checks
it started: before starting one again, check its process (the pid in `now.md`) and its log or reply file, and wait
for it or use its result. With
several deliveries, finish the unfinished one first, then start the next one its run log names.

## Limits

These keep a run from grinding. Reaching one is not a stop: record the open items and move to the next step.

- A failing check: three fix attempts, each with a different idea, then record it as open.
- QA: one fix round for its failures and one re-run of the items that failed, then record what still fails.
- Review: one full review, then one delta review of the fixes it and QA led to; no third round. A fix after the
  delta review is proven by its test and the checks, and the report lists it as fixed after the last review.
  Fixes for a failing check or a compile error, a resolved conflict, the cleanup and an update onto a moved base
  start no review or QA round: the last verify is their proof (`steps/review.md`).
- No progress for three rounds in a row: step back, change the approach once, then go to the report.

## Guard rails

**Scope**
- Work only in the delivery worktree. Never edit the user's own checkout, other branches or other worktrees; the
  exceptions are the base worktree set up adds for the baseline and removing cpipe's own merged worktrees there
  (`steps/setup.md`).
- Change only what the plan's scope names. If the scope must grow, say so in one line and note it in the run log.
- Format and lint-fix only the lines the change touches. Never reformat files or the repository, even when the
  formatter's baseline is red, unless the task asks for it.

**Safety**
- Never merge, push, open a pull request, deploy or message anyone unless the user asked for it in this work.
  When they did, do it once its condition holds (`steps/report.md`).
- Never put credentials, tokens or secrets in code, commits, logs, briefs or the report. Never print `.env`
  contents.
- Never run destructive commands outside the worktree (deleting data, resetting branches, dropping databases),
  apart from the set-up tidy-up and removing the base worktree.
- Never add AI attribution to commits or pull requests.

**Honesty**
- Never report a check as passed that you did not run and read on the current tree.
- Never skip, weaken, delete or mark as expected-failure a test to make a check pass.
- A profile's or subagent's "done" is a claim until you have checked it yourself.

**Cost**
- Every reply resends the chat: fewer replies and a smaller chat are the saving. Wait by ending the turn, never
  by polling (Waiting ends the turn).
- With a build profile, this chat never reads the project's code, and reads `plan.md` only by the sections it
  needs: the planner or the builder explores it once per run (`roles.md`, How the work is split). Without one,
  read once, in explore; after it, read code only as the diff and the lines a finding or a failure names.
- One session per role per run: every follow-up and the next delivery resume it (`roles.md`, Follow-ups).
- Code is handed back only when it builds: the builder runs the build or typecheck and the narrow tests before
  it replies (`steps/build.md`).
- Review and QA rounds are capped (Limits); the checks, not another round, prove fixes for checks, compile
  errors, conflicts and the cleanup.
- The project's own commands for checks: no scripts, harnesses, receipts or manifests of your own around them,
  and no extra full suites on other trees (`steps/verify.md`).
- Narrow tests while building. The full suite and the build run only in verify, one at a time: in the first
  verify and the last one, on the final tree; the verifies between them run the related tests
  (`steps/verify.md`, Fast checks).
- A profile's result is its output or its reply file (`roles.md`); never search its session history for it.
- A step that does not apply, or mechanical work (format, commit, pull request body, a rebase without
  conflicts), gets no profile (`roles.md`).
- Long output to a file, the tail in the chat (Run log). Each user skill loaded once per run, at the step that
  uses it. Git output that grows with the repository (worktrees, branches) is filtered before it reaches the chat.
- The baseline runs in its own worktree, so the build never waits for it; every background job has a time limit
  (`steps/setup.md`).
- After the context is compacted, reload only this skill and the current step's file, never the list of skills the
  compaction notice gives (After a compaction).
- The roles file and the lessons are read once, in steps 1 and 2; their content is in the run log and the plan.
