---
name: run
description: "Deliver a code task unattended: plan, test, build, review."
version: 4.50.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delivery, tdd, code-review, verification, autonomous, self-learning]
---

# cpipe: run

Takes one development task from request to code that is ready for the user's review. You run the flow in this
chat with your normal tools; this skill and its files are your instructions and nothing else drives it. Steps can be
handed to the user's own profiles (see Roles), but you stay in charge of the run.

This chat can be any of the user's profiles, each with its own persona, standing rules, skills and tools. For a
run they set only your tone: the steps, the roles, the checks and the bar for done come from cpipe's files and
the Hermes root (Roles), the same whichever chat runs it. Where this chat's own rules would ask first, hand the work
elsewhere or drop a step, cpipe's files win; a rule that forbids something (a push, a destructive command, a
secret) still holds.

## The one rule: keep going

An obstacle is something to work around, never a reason to stop. A blocked command, a broken test runner, a
missing dependency, a flaky check, a tool error, a profile that returns nothing, a reviewer asking
for evidence this machine cannot produce: work around it (`steps/verify.md` has a table for the common ones), write
what you could not verify in the run log, and continue with the next step.

## The user's instructions are the contract

Every instruction the user gives about this work, in the task or in any later message, holds until the user
changes it. A later instruction replaces an earlier one it contradicts ("merge it" after "do not push" asks for
the push and the pull request a merge needs): act on it, never ask which one holds. An answer that rules out
one fix ("keep what is current" to a fix that needs a new setup) narrows how, never what: find the fix that holds
within it (`steps/design.md`), and leave the gap open only when the user says so. That covers how to work (in parallel, which base, draft pull
requests), what the change must respect (keep the client's behavior, follow an existing rule) and how to finish (push, open a pull request, merge when a
condition holds). Write each one word for word in the run log and under `Instructions:` in `now.md` the moment it
arrives: only what the user wrote, never your reading of it (that goes in the run log). `gate` and `brief` refuse
a line the user never wrote, since every role takes these lines as settled. One they did write that is refused
(their message did not reach the chat's history) still binds the run: move it from `now.md` to the run log with
their words and carry it out, never drop it or go back on it. Check every plan, decision, fix and report against them. Where an instruction differs from a default
in these files, the instruction wins. Only the Safety guard rails still apply, and an instruction to push, open a
pull request or merge gives the permission they ask for.

An instruction that settles a question is its answer. When a finding is one an instruction decides, fix it the
way the instruction says, for example a change that rejects what the user said must keep working. Never hold it
for a decision, a policy or an approval the user already gave. The run is finished when every instruction is
met. An instruction stays unmet only for a reason the report names: something only the user may do, or
something this machine cannot do; until then the run loops (Limits).

**The user's rules.** The user can keep standing rules for cpipe runs, and only cpipe, one `- ` line each, like
`cpipe.yaml`: `<Hermes root>/cpipe/rules.md` for every run, `$HERMES_HOME/cpipe/rules.md` for a profile's own
sessions, and `$(git rev-parse --git-common-dir)/cpipe/rules.md` for one repository's runs (`/cpipe-rules`, edited
in words). They are pinned in every session of the run, a role's too, each tagged with its scope. Where two differ,
the more specific wins: the project's, then the profile's, then everywhere's. Each is an instruction the user gave
for this run, held like the ones above: it can change how the run works (`commit and push after each commit`: commit
each verified build round, using the commit step in `steps/publish.md`, and push it), and a push, pull request or
merge it asks for is asked for. This task's own instructions win where they differ. They are not copied into
`now.md` (only this chat's words go there); the report checks them like the instructions. A role's session does none
of the run's own work (commit, push, merge): the run does it after the role replies.

## Unattended: the task is the last message

The user sends the task and waits for the report. Never stop to ask: no plan approval, no "should I". The one
question the run puts to the user is a spec question, where the user's words and the code disagree and nothing
settles it (`skill_view(name='cpipe:run', file_path='spec.md')`): posted as a `❓` line while the run keeps
working on its recommended answer, and waited on only when nothing else is left. Never call a tool that waits for
the user (a clarify or question tool, a command that needs the user's approval): it halts the run until they
answer. Use another way, or skip that part and report it: the board offer after the report is the one clarify
call outside `--spec` (`steps/report.md`, Board offer). With `--spec`, the plan profile works out the spec
with the user first, its own way, its questions put to the user with the clarify tool (`spec.md`, --spec).
Write commands that need no approval: read and edit files with your file tools
(read, search, patch, write), and run one program with its arguments, never `execute_code`, inline code
(`python -c`, `node -e`), `sh -c`, heredocs or `if`/`;` chains; a script you need goes in a file you then run.
A command blocked because no one can be asked (a one-shot or scheduled session: "runs without a user present")
was never put to the user: do the same work the standard way above, never run it again as it was, and never
pass it on as the user's approval. Only an approval Hermes asked the user in this chat, refused or timed out,
is the user's to give: do every part of the run that does not need it, never reach the same result another
way, and end the turn with this line:
`⚠️ <n>/11 <Step> · needs your approval: <what the command does> · say continue to be asked again`.
Every choice is yours:

- **Open questions** (which base, which of two behaviors, a product detail, how old data or another role should
  be treated): decide from the user's instructions first, then the request, the code, the project's history and
  the conversation. Pick the most likely and reversible answer, record it as an assumption, and keep going. One
  that changes what the user would see, with nothing to settle it, is also a spec question (`spec.md`).
- **Things only the user may do**, unless the user asked for them: credentials or secrets, anything destructive,
  and anything that leaves this machine (push, pull request, merge, deploy, messages). Never do them. Skip that
  part, finish everything else, and name it in the report's `⚠️ Done except` status line. How the code should
  behave is never one of them: that is an open question.
- **A security problem in code this change does not touch:** finish the rest and lead the report with it. A
  problem this change causes is fixed in this change.

A push, pull request or merge happens only when the user asks for one; a task to fix an existing pull request or
make it right asks for a push to it (`playbooks/review.md`). A message from the user mid-run is steering, not a stop
(unless it ends the run, below): add its instructions to `now.md`, answer it in one line in the same reply as the
run's next tool call, and go on as Continue says. Never end the turn on the answer: the run only resumes from a turn
that is still working, and a message that cut off a turn means that turn's work is undone. A request to do something
now (edit the pull request, change its title, merge, look at a file) is steering too, however it is worded and
whatever context blocks the chat adds to it: do it as part of the run, then make the run's next tool call in the
same reply; a reply that only reports the result stops the run. A question about the run ("is it done?") is handled
the same way while any instruction is unmet. Steering that adds work (another bug, another behavior) is either added
to the plan as numbered steps with their acceptance criteria, or, when it is a different problem, kept for a
follow-up after the report; say which in that line and note it in the run log. Steering that asks to reproduce or
try a behavior (in a browser, an emulator, a simulator or on a device) is QA's work (`steps/qa.md`): give it to the
QA profile in a brief of its own, running alongside the build, never in this chat, whose every reply resends the
whole chat; its result comes back as a QA reply. Steering that sets up the user's own local testing (start or
restart the app, a test login, a temporary local-only edit such as skipping a password check) is not a change to
deliver: do it yourself in this chat at once, the way the user said and no bigger, never through a role and never
wrapped in setup they did not ask for (another database, a bootstrap script, network blocks). Keep a temporary edit
out of every commit: note its file and line in `now.md`, name it in each later build brief as never to be staged,
check `git diff --cached` before each commit, and revert it once the user and QA are done with it.

**The user ends the run.** One message is not steering: one that tells you to stop or cancel the run, or not to
use cpipe ("don't use cpipe", "do it without cpipe"), or `/cpipe-stop`. It ends the run now, whatever step it is
at and whatever the gate says: close it (`active <run dir> --done`, Facts), kill the background processes the run
started (its step, tick and limit commands) and add `stopped by the user` to the run log. Then do what the message
asks directly, as After the report says, with no run steps, roles, briefs, reviews or report. A notification from
the stopped run is ignored, and a later plain "continue" continues the user's own work; only `/cpipe-continue`
reopens the run: its message starts "Continue the cpipe run in this chat", and you follow it, whatever the user
typed after it.

**Notes that are not for this run.** The chat can receive text mid-run that is neither the user nor this task: a
note on a kanban card, a board digest, another card's crash or status. If it names no card this run created (run
log), it is not for this run: do not look it up, act on it or ask about it. Post
`ignored: <what it was>, not from this run` with your next tool call and continue. The task in the run log is the
task; nothing that arrives mid-run replaces it.

**Waiting ends the turn.** A reply without a tool call ends your turn. Every reply resends the whole chat, so a wait
costs a full reply each time you look: never poll, sleep, list processes or call the process tool's wait in a loop.
When a profile or check this run started is running with a completion notification and nothing in the run can move
until it finishes, end the turn with exactly this line, the time being when its time limit ends:
`⏳ <n>/11 <Step> · waiting on <who or what> · done by <clock time> · resumes on its own`, such as
`⏳ 8/11 QA · waiting on @tester's checks · done by 12:29 · resumes on its own`. Never a line of your own:
without the time and the resume note a wait looks like a stop. Its
notification starts the next turn. With nothing running, a reply without a tool call ends the turn on nothing, so
end a turn only on such a wait, a needed approval (above), the pause message, the `❓` waiting-on-your-answer
lines when `gate` says `ASK` (`spec.md`), or the report. A wait line with
nothing of this chat's running comes back to you as unfinished. Progress updates go in
the same reply as your next tool call. Without completion notifications, wait on that one process id with your
process tool. Whatever you wait on ends on its own: every install, build, test and profile, yours too when you build
yourself, runs under `limit.pl` (`steps/setup.md`, Time limit), so a hang still ends it, its notification still
comes and it never slows the user's machine.

**Progress while waiting.** A wait that can last past 10 minutes (a profile, the build) gets a tick beside it:
`sh <skill dir>/bin/tick.sh <run dir> <worktree>` in the background with a completion notification
(`steps/setup.md`, Progress tick). It sleeps 10 minutes, then prints what changed: the diff's size and the newest
logs with their last line. When it wakes the run and the work still runs, post one line from its output alone,
read nothing else, and start the next tick in the same reply:
`⏳ <n>/11 <Step> · <m> min in · <what changed> · done by <clock time>`, the change such as
`4 files changed (+180 −12), 71 tests pass` or `the suite's log silent for 9 min`. One tick runs
at a time, whatever it watches. Once nothing long runs, stop it with your process tool in the same reply as your
next tool call; a tick that arrives with nothing running gets no line and no next tick.

**Turn limits.** A turn has a cap on replies that call tools, so a long run can end its turn mid-step. Spend
replies, not calls: put independent tool calls (reads, ready checks to start) together in one reply. A batch may
run concurrently: write a brief before launching its reader, and wait for any input a call depends on. Short
facts and bookkeeping run in the foreground, without a background job and its completion wake. Long work
(installs, checks, profiles, anything that can take over a minute: a tool call is cut off after a few minutes)
runs in the background with a completion notification: when a turn ends early, the
next notification starts a new turn and the run picks up there. A turn that ends with nothing running has no
notification to resume it, so start the next long piece of work before a stretch of short steps. Do not start a
timer or look for a tool to make one; the progress tick is the only one. When the cap ends the turn (a message
asks for a final summary without tools), the final reply is this pause message and nothing else:

```
⏳ Paused by the turn limit at <n>/11 <step>, not finished: <goal>
Done: <what this turn finished, in one line>
Next: <the exact next action>, then <what is left until every instruction is met>
Decided: <each open question or unexplained result: the decision the next turn acts on, and why>
Resumes: when <what is running> finishes | on its own (nothing is running)
```

Leave out `Decided` when nothing is open. An open item is never only "unresolved" or "cause unknown": it carries
the decision, made the way `steps/report.md` (Finish before reporting) makes it. The pause message is not the
report. A notification after the report gets one line: `cpipe: already reported (<status line>), nothing left to
do.` The report is the message written from `steps/report.md` once every instruction is met or has its reason.

**A notification resumes the run.** A completion notification (a check, a profile), or a new turn that starts
with a note that the last one hit its limit, is never a cue for a status reply. While the run log has unfinished
steps or `now.md` an unmet instruction, say `Resuming at <n>/11 <step>: <what woke the run>` and continue
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
from one delivery to the next (`roles.md`, Follow-ups), so a builder reads the codebase once per run, not once per
issue. When the user asks for them in parallel, in their own words or with `--parallel`, they run as lanes
(below), never side by side in this chat. Each run log names the others (`Delivery 2 of 3; others: <branches>`).
No combined or integration branch unless the user asks for one: each delivery is verified, reviewed and merged on
its own, and one whose base moved is updated onto it (`steps/publish.md`, Merge).
Deliver two issues together only when one cannot work without the other (the same root cause or the same lines)
or the user asked for one pull request for all of them in so many words, and record why in Approach. "Create a
PR" or "open PRs" for several issues asks for a pull request per issue, never a combined one.

With `--parallel` at the start of the task, or parallel asked in words (mid-run included), each issue is delivered
in a sub-agent of this chat instead (a lane), up to four at once, one issue included, and this chat is only their
status monitor: it starts them and posts their status and reports, and a delivery it was running becomes a lane:
`skill_view(name='cpipe:run', file_path='parallel.md')`. A sub-agent started from a lane's brief follows the
same file, In a lane.

## Flow

Track the steps with your todo tool if you have one, one step active at a time.

**Status lines.** The user follows the run from these lines alone, so each one says what happens, who does it
and what came out, in the same shape: `[icon] <n>/11 <Step> · <parts>`, the step name as in the table below (a
fix round is `6/11 Build · fix round 2`, a merge is `11/11 Report`), plain words, no file paths or tool names.
A profile (a bot) is always written with its `@`, `@builder`, never the bare name, in every line, question and
report table you show the user, since the chat links an `@name` to its bot:
- starts, with your next tool call, no icon: `6/11 Build · @builder · tests first, then the change` (who: the
  profile, `this chat's model` for a role session without one, or `this chat`);
- ends: `✅ 6/11 Build · 29 min · 4 files, 3 new tests RED → GREEN`, `✅ 9/11 Review · 12 min · 2 findings to fix`;
- does not apply: `— 3/11 Design · skipped: no screen changes`;
- a problem the run handles (a profile failed, a check would not run, a limit reached): `⚠️ 9/11 Review · @reviewer
  failed to start (provider error) · running it in this chat`;
- results that arrive while other work runs (a reviewer's findings, a check's result): their own step's line,
  `✅ 9/11 Review · @security · 3 findings to fix`, `⚠️ 7/11 Verify · related tests timed out · retrying narrower`;
- several things running at once: one ⏳ line for the step that ends last, naming the others
  (`⏳ 9/11 Review · waiting on @reviewer, @security and the server tests · done by 21:49 · resumes on its own`);
- a wait and its progress: the ⏳ lines below.

Every line you post while the run is going, after a compaction too, is one of these: never free text such as
"Related server tests running" or "Security review identified three issues".

Icons mark a state and nothing else: ✅ done, ⏳ waiting, ⚠️ a problem, ❌ failed. No other emoji anywhere the
user reads (status lines, pause message, report), and none for decoration.

A clock time is the user's local time as `HH:MM`, without a time zone. Post each line once: a turn that resumes
after a wait never repeats the ⏳ line it ended on. Each step's instructions are a file in this skill: load it with
`skill_view(name='cpipe:run', file_path='steps/<name>.md')` when you reach a step you do in this chat, not
before; a step a profile does needs only `roles.md` here, and the profile loads its own file. Never work a step
from memory, and never load a file whose text is still in the context: load it again only when its text is gone
(a follow-up, or a compaction that dropped it), and then only the current step's file. When `skill_view` answers
that the content is unchanged without returning it, or this chat has no `skill_view`, read the file itself with your
file tool from this skill's directory (the start message names its `SKILL.md`). Every report is written from
`steps/report.md` loaded right before it.

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
| 11. Report | Commit, a draft pull request and a merge only if asked, the report. | `steps/publish.md`, then `steps/report.md` | |

The review only reads the code, so it never waits for the tests: start it in the background as soon as the
first verify's narrow tests, light checks and completion gate pass, then start the related tests and the build. QA
runs the change, so it starts once those finish (one heavy job at a time). Nobody edits the worktree while they
read it: for a look change with a mockup, the design profile checks QA's after shots against it as soon as QA
replies (`steps/design.md`, Design check). Wait for all of them, then fix everything in one round (new failures,
findings, QA failures, the design check's fixes), verify, and re-run only the QA items that failed together with
the delta review.

When the user asks for a draft pull request on the first commit, early or first, it opens right after the first
verify passes its gate, and later rounds push to it (`steps/publish.md`, Early draft); step 11 finishes it. Asked
later, it opens once nothing runs in the worktree, never by pausing a profile.

Every step runs on every task. A step that does not apply (design for an internal fix, QA for a docs change) is
skipped with a one-line reason in the run log, never silently, and gets no profile. Steps 7 and 11 are always
yours: the checks and the final word are never handed to anyone else. Steps one session does in one brief (a
planner's explore to plan, a builder's explore to build) share one start line.

## The user's own skills come first

cpipe sets the order of the steps; the user's own skills say how a step is done, whichever profile this chat is. At
the start of each step you do in this chat, find the skill that covers that step's work (writing a pull request or
commit message, code review, security review, UI design, testing a web app, a project's conventions) among all the
skills in this Hermes install, not only your own list: the user's skill for it can live in another profile. One
command lists them by description, each skill once (here with the words for a pull request):

```sh
find <Hermes root>/skills <Hermes root>/profiles -maxdepth 5 -path '*/skills/*' -not -path '*/skills/.*' \
  -name SKILL.md -exec grep -E -m1 -H '^description:.*([Pp]ull[- ]?[Rr]equest|\bPRs?\b)' {} + 2>/dev/null \
  | sort -t: -k2 -u
```

Take the one about that work itself (a skill for pull-request content over one that runs a whole workflow or drives
a tool) and load it with `skill_view` when it is in your list, otherwise read its `SKILL.md` with your file tool.
Once per run: note each loaded skill in `now.md` (Run log) with the rules from it that apply to this task, one line
each, and work from those lines afterwards, also after a compaction; never load it again. Never load one before the
step that uses it or for a step a profile does: that profile uses its own skills, and a skill loaded early is resent
with every reply until the report. Running profiles and processes needs no skill; `roles.md` has the commands. Where
it and a cpipe step differ on how to do the work, follow the user's skill, including its scripts and checks. cpipe
still decides the flow and the guard rails: a user skill never authorizes a push, merge or message the task did not
ask for. Take from a user skill how the work is done, not a different flow: skip its parts about task boards, cards
or handing work to other agents. Note in the run log which user skills you used.

cpipe is the floor under a thin setup: when none of the user's skills covers a step, cpipe's own text is the default
(the step's file, a stance from `header.md` for a role with no profile of the user's, and for how a screen looks
`guides/ui.md` in this skill). A role the user gave a profile works by that profile's own setup. Note it in the run
log (`Skills: no UI design skill, cpipe's guide`); the report names it when the run needed it (`steps/report.md`,
Needs attention).

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

**The Hermes root** is the folder all the user's profiles share, so every chat runs with the same roles, budget,
learnings and skills: two levels above `$HERMES_HOME` when that is a profile's folder (`…/profiles/<name>`), else
`$HERMES_HOME`, else `~/.hermes`. The start message names it. The roles file is `<Hermes root>/cpipe.yaml`, or
`$HERMES_HOME/cpipe.yaml` when this profile has one of its own.

If the roles file exists, it names a Hermes profile for some of the roles in the flow table. Hand those steps to the
named profiles as `skill_view(name='cpipe:run', file_path='roles.md')` describes. Build, QA and review always run in
a session of their own: without a profile for them, on this chat's own profile and model (`roles.md`). Other roles
the file does not name are done as the flow says without them. Either way, tell the user once at the start who does
each step. When the task message starts with `--kanban`, the named profiles get their steps as cards on the user's
kanban board (`roles.md`, On the board); without a roles file there is nothing to hand over, so say that in the
roles line and continue. A `--budget <tier>` at the start of the task, or `budget:` in the file, sets how hard the
profiles reason (`roles.md`, Budget). A plan profile explores and plans on its own model and the builder builds from
its plan; without one, the builder does the reading and planning too. This chat never reads the project's code
(`roles.md`, How the work is split): it runs the flow, the checks and the facts, and stays small, so it rarely
compacts.

## Run log

One log per run at `$(git rev-parse --git-dir)/cpipe/run.md`, run inside the delivery worktree. Never committed;
it survives a restart. Append, never rewrite, and start each row with the clock time (`HH:MM`), which the run
graph is drawn from (`steps/report.md`, Run graph):

- the task and every instruction from the user, word for word, the roles, the base and the worktree, and every
  card id when on the board;
- the baseline and the base worktree's path, and the paths of the plan (it holds the flow map) and the design note;
- one row per round: what changed, why, the check results;
- every workaround and everything you could not verify, with the reason;
- QA results, review findings and what you did with each;
- the misses and learnings written.

The plan has its own file beside it, `plan.md`, so a builder reads the plan and not the whole log. Beside both is
`now.md`, rewritten as the run moves: at most 25 lines, plus the instructions. Its first section is
`## Instructions`: the user's instructions word for word, one per line as `- [ ] <their words>` (not yet) or
`- [x] <their words>` (met); the run text `/cpipe` puts before the task is cpipe's, not theirs. Then `## `
sections for the
current step and its next action; the processes running (process id, log or reply file); the roles line; the rules
taken from user skills; and the open items. Every reply that ends a turn (a wait, a pause, the report) first
writes its line in `now.md`'s current step: that write is what lets the run's gate check the turn's end.
Rewrite it once per step, as one patch in the same reply as the step's first tool call, and in the reply that
ends a turn; a process that starts or finishes in between goes in at the next of those, never in a write of its
own (each write is a reply that resends this whole chat). It is what you read to resume, instead of the whole log.

## Facts

Questions with an exact answer are answered by `bin/cpipe.py` beside this file, never by judgment. Run it as
`python3 <skill dir>/bin/cpipe.py <command> …` from the worktree (`<skill dir>`: this file's folder, which the start
message names; `bin/` also holds the time limit, the progress tick and the role launcher); it reads files
and prints data and a verdict, and you act on what it prints:

- `results <run dir>/results/<check>.<tree>.json <junit|jest|tsc|eslint|lines> <files>`: a check's results from
  the runner's machine-readable output (`<tree>` is `base`, `head`, `base-alone` or `head-alone`), so a cut-off
  log never decides anything (`steps/verify.md`, Names from the runner).
- `compare <run dir> <check>`: every failing name on the delivery as **new**, **baseline**, **flaky** or a rerun
  to do (`rerun-head`: run it alone on the delivery; `rerun-base`: run it on the base). Never undecided: do the
  reruns and compare again.
- `track <run dir> add <step> <text>`, `set <id> done|skip|user <evidence>`, `open [<step>]`: the checklist in
  `track.json`. When a step starts, add its Done when items, one per condition; each instruction in `now.md`
  the moment it arrives (step `instruction`, its words; `gate` holds until each is on); at the report each merge
  condition. An item closes only with its evidence (a result, a file, a count):
  `done` when met, `skip` only as `n/a: <why it does not exist in this change>` or `machine: <what this machine
  cannot run>` and never for a user's instruction, `user` for what only the user may do, its evidence starting
  with its kind: `credentials:`, `destructive:`, `external:` (push, pull
  request, merge, deploy, messages) or `approval:` (one Hermes asked the user, refused or timed out), or a
  spec question `answer: <R or C id of spec.md>: <question>` (`spec.md`). `track` and
  `gate` refuse any other: which fix, the scope or how the code behaves is the run's to decide. Unclear, "not established" or "inconclusive" stays open and is work for the loop.
- `gate <run dir>`: the run's verdict. `FIX: …` is the loop's next work; `WAIT: …` ends the turn on a wait line;
  `SPEC: …` the spec is still drafted with the user (`spec.md`, --spec);
  `ASK: …` is only the user's answers left: end the turn on the `❓` waiting lines (`spec.md`);
  `NEEDS-USER: …` is what only the user may do; `PASS` means every condition holds: merge when asked, then report.
- `ci <run dir> <pull request>`: waits for the pull request's CI, saving `results/ci.json` on every read and
  reading through GitHub's passing errors; a `WAIT: CI …` turn ends only with it running (`steps/publish.md`).
- `report <run dir>`: the report's skeleton with its facts filled in; `report <run dir> --check`: `PASS` when
  `<run dir>/report.md` keeps those facts and has every section and cell written, else `FIX` with what is wrong
  (`steps/report.md`, Report).
- `cost <run dir> <worktree> --start`: capture private usage baselines before role work; later calls add only
  missing baselines. `cost <run dir> <worktree>` reads measured deltas per profile, model, provider and task,
  with coverage warnings. Without capture it labels legacy lifetime counters (`steps/report.md`, Cost).
- `active <run dir>` once the run log exists (`report <run dir> --check` closes it when it passes, and
  `active <run dir> --done` when the user ends the run, with the chats it was compacted from): registers this chat's
  run, so the run's rules, the user's instructions and the open items stay pinned in this chat's system
  prompt whatever a compaction drops, and `gate` and the watcher check the run. `/cpipe` marks the chat's run as
  started; until `active` registers its folder, every turn is told to set it up and register it, and nothing else
  counts. A `project:` line it prints names the user's project that holds the repository while this chat is in
  none: move this chat into it before anything else (`steps/setup.md`, This chat's project), on a continue too.
- `port <source> <base> [--repo <source repository>]`: each line the source change (a commit, or `<from>..<to>`)
  adds that this tree's diff from the base does not contain, by file, the code's before the tests'
  (`playbooks/port.md`).
- `lanes <batch dir> [--at-once <n>] [--report <lane>]`: each lane of a `--parallel` batch as queued, running,
  reported or stopped, then what to do: `START`, `RESUME`, `WAIT` or `DONE` (`parallel.md`).

Long command output goes to a file beside the run log (`logs/<step>-<check>.txt`); read the result lines or the
tail, not the whole output. A command you wait for prints them itself (`grep` the summary and failures, `tail`),
so no reply goes to reading the file. The run log names the file.

## After a compaction

When the chat is compacted, the step files and skills loaded so far are dropped, and the compaction notice may ask
you to reload every skill this chat ever loaded. Do not: that refills the context and brings the next compaction
within minutes. The run's rules, the user's instructions and the open checklist items stay in the system prompt
(Facts, `active`). Read `now.md`, reload `cpipe:run` and the active step's file (from the plugin's directory when
`skill_view` returns no text, Flow), and continue. The roles, the plan's questions from the learnings and every
file you read are in the run log or the plan. Read a file again only for the lines the next action needs.

## Learning

cpipe learns from every miss: a learning is one line that adds a check, a place to look or how the user wants
something done, kept under the Hermes root. Where they live, how they are read, written and retired, and feedback:
`skill_view(name='cpipe:run', file_path='learning.md')`, loaded when explore reads them, when a miss is found and
at the report, never before.

## After the report

The report ends the run. A plain message after it, even about the same work, is the user's own request, not the
run's: do it directly in the worktree the way the chat would without cpipe (the change, its narrow test, the commits
and pushes the user asked for), with no run steps, roles, briefs or reviews. Only `/cpipe` reopens the run (a
follow-up with `/cpipe <what to change>`, or `/cpipe-feedback <what they found>`). For a `/cpipe` follow-up, reuse
the worktree and the run log (or, if it was removed after its merge, a new worktree from the base), record the miss
and its learning (`learning.md`, Feedback), update the plan, then run build, verify, QA and a delta review on the
follow-up only, and report again. Reproduce what the user describes before answering. If it can mean two things,
check both and answer with what you found; do not ask which one they meant. A different task starts a new run. "Fix
it" after a report that said something could not be fixed reopens the loop: never answer again that it cannot be
done with nothing changed. Take an approach not tried yet, at least the one in `steps/review.md` (When fixes fight),
and loop until the goal is met.

## Continue

A "continue" (or `--continue`), a `--restart` or a chat restarted mid-run: load
`skill_view(name='cpipe:run', file_path='continue.md')` and do what it says. A notification mid-run resumes the
run as Unattended says.

## Limits

The run loops until the goal is met: fix, verify, check the goal and every instruction in `now.md`, and go
round again while anything is unmet. Only what the user alone may do (Unattended rules) or what this machine
cannot run ends a loop early. The limits below keep one approach from grinding, never the run from its goal:
reaching one means change the approach and loop again, not report. Each round changes something; an attempt
that already failed is never repeated.

- A failing check: three fix attempts, each from a Cause line (`steps/build.md`, Root cause) with a different idea;
  then write the Cause again from scratch and change the approach (another design, the builder on another profile,
  the fix in this chat) and loop again.
- QA: a fix round for its failures and a re-run of the items that failed, as many rounds as it takes, each with a
  different idea after the third.
- Review: one full review, then one delta review of the fixes it and QA led to; no third round. A fix after the
  delta review is proven by its test and the checks, and the report lists it as fixed after the last review.
  Fixes for a failing check or a compile error, a resolved conflict, the cleanup and an update onto a moved base
  start no review or QA round: the last verify is their proof (`steps/review.md`).
- A reply that offers less than the goal (part of it, a gap left open and disclosed, "not feasible within
  the constraint") is not a result, and neither is no progress for three rounds in a row. Step back to the
  user's words in `now.md`: every constraint, test and Done when in the way names the instruction it serves,
  and one that none carries is the run's own (a limit you inferred, a test stricter than the instruction
  asks). It gives way: drop it or rewrite the test to what the instruction says, and log why. Then re-plan
  (`steps/plan.md`, step 5, on the plan profile) from those words, the obstacle and every approach tried,
  taking the simplest design that meets them, and loop again. A brief asks for the goal, never offers a way
  out ("otherwise explain the obstacle"). Only the user ends a goal short.
- When what is in the way is the user's own words, two of their lines that cannot both hold (a smallest fix
  with no new setup, against a guarantee only a new setup gives), no re-plan can meet both: that is a spec
  question (`spec.md`, Reconcile, Conflict), asked once with the recommended answer. Ask it the first time a
  re-plan hits that obstacle, keep working on everything else, and wait on it only when nothing else is left
  (`gate` says ASK). A behavior the base branch has too is not a regression of this change: closing it is the
  goal only when an instruction asks for it.
- A gap a builder or re-plan discloses counts as the goal unmet only when it traces to the user's words; one that
  traces to a finding's own "should" is **consider** (`steps/review.md`, A finding is not a requirement). A
  re-plan never grows the design past what the user asked: no new subsystem the goal did not name. The same
  holds for what a builder reports as remaining: each item is sorted like a review finding before it becomes a
  brief, and one that traces neither to the user's words nor to the change's own regressions goes to the report,
  not into another round. A brief never asks to "reconcile other paths" the goal does not name.
- Each round closes more than it opens. A round whose fix led to new findings, twice in a row, or a fix that
  breaks working behavior: go back to the last green tree and take the narrower approach (`steps/review.md`,
  When fixes fight). Looping is for the goal, not for every finding a reviewer can raise beside it.
- **Rounds.** `step.sh` ends with the run's round counts (`cpipe.py rounds`). A role it names at its cap gets
  no further round. Build at 3 (since the last plan or review): the approach is not closing, so re-plan
  (`steps/plan.md`, step 5) before another build brief, unless all that is left is a failing check or compile
  error of the last round. Review at 2 and security at 3 count over the whole run, so a re-plan never buys
  another review: their open findings are fixed and proven by their tests, or listed as **consider**, with no
  further review. A re-plan starts a new build count only; it never grows the design to answer a finding the
  user's words do not carry.

## Guard rails

**Scope**
- Work only in the delivery worktree. Never edit the user's own checkout, other branches or other worktrees; the
  exceptions are the base worktree set up adds for the baseline and removing cpipe's own merged worktrees there
  (`steps/setup.md`).
- Stick to the scope: change only what the plan's scope names. If the scope must grow, say so in one line and note
  it in the run log. A scope or "smallest fix" bounds what changes beside the goal, never the goal: what the goal
  needs is in scope. A fix that takes a large refactor is a follow-up delivery in the report, never this run's
  (`steps/review.md`, A large refactor is its own delivery).
- Format and lint-fix only the lines the change touches. Never reformat files or the repository, even when the
  formatter's baseline is red, unless the task asks for it.

**Safety**
- Never merge, push, open a pull request, deploy or message anyone unless the user asked for it in this work.
  When they did, do it once its condition holds (`steps/publish.md`).
- Never put credentials, tokens or secrets in code, commits, logs, briefs or the report. Never print `.env`
  contents.
- Never run destructive commands outside the worktree (deleting data, resetting branches, dropping databases),
  apart from the set-up tidy-up and removing the base worktree.
- Never add AI attribution to commits or pull requests.

**Honesty**
- Never report a check as passed that you did not run and read on the current tree.
- Never skip, weaken, delete or mark as expected-failure a test to make a check pass.
- A profile's "done" is a claim until you have checked it yourself.

**Cost**
- Consume the completion reply already returned; read a narrow reply file only when it points there. Do not
  reload logs, `now.md` or the plan just to acknowledge it. Update state at the actual step transition (Run log).
- With a build profile, this chat never reads the project's code, and reads `plan.md` only by the sections it
  needs: the planner or the builder explores it once per run (`roles.md`, How the work is split). Without one,
  read once, in explore; after it, read code only as the diff and the lines a finding or a failure names.
- One session per role per worktree: every follow-up, the next delivery and a later run there resume it (`roles.md`,
  Follow-ups).
- Code is handed back only when it builds: the builder runs the build or typecheck and the narrow tests before
  it replies (`steps/build.md`).
- Review and QA rounds are capped (Limits); the checks, not another round, prove fixes for checks, compile
  errors, conflicts and the cleanup.
- The project's own commands for checks: no scripts, harnesses, receipts or manifests of your own around them,
  and no test runs on other trees (`steps/verify.md`).
- Narrow tests while building; every verify runs the tests related to the change, never the full suite, which
  CI runs. The build runs once, in the first verify (`steps/verify.md`, Fast checks).
- A profile's result is its output or its reply file (`roles.md`); never search its session history for it.
- A step that does not apply, or mechanical work (format, commit, pull request body, a rebase without
  conflicts), gets no profile (`roles.md`).
- Long output to a file, the tail in the chat (Run log). Each user skill loaded once per run, at the step that
  uses it. Git output that grows with the repository (worktrees, branches) is filtered before it reaches the chat.
- The baseline runs in its own worktree, so the build never waits for it; every background job has a time limit
  (`steps/setup.md`).
- After the context is compacted, reload only this skill and the current step's file, never the list of skills the
  compaction notice gives (After a compaction).
- The roles file and the learnings are read once, in steps 1 and 2; their content is in the run log and the plan.
