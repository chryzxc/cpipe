# cpipe: the report step

Step 11 of `cpipe:run`.

Before writing the commit or the pull request, find the user's own skill for commit messages or pull-request
content among all the skills in this Hermes install, whichever profile this chat is (`cpipe:run`, The user's own
skills come first, has the command), and load it. If one exists, it decides the title, body, template and any lint
script to run before `gh pr create` or `gh pr edit`; the sections below are the fallback.

## Commit

Commit on the delivery branch in the worktree. Use the project's commit convention; otherwise Conventional
Commits: `<type>(<scope>): <imperative summary>`, a truthful type, and a body that says what changed and why.
Only the scoped work, no AI attribution, no secrets. Follow the project's hooks; if a hook fails, fix the cause
rather than skipping it.

## Pull request (only when asked)

Only if the user asked for one in this task: push the branch and open a **draft** pull request against the plan's
base, one per delivery (`cpipe:run`, One issue, one delivery). Title in the commit style. Body: the issue it
resolves, in the project's linking style (`Closes #123`), what changed and why, how it was verified (each check with its state),
anything not run here and how to check it, open findings. No local paths, internal tool names or chat details.
Never merge, mark ready or request reviewers unless asked. If the push or the pull request fails, report the
error and the exact command to retry; the work stays committed locally.

For a visible change, put QA's before and after screenshots in the pull request the way the user's
pull-request skill says to attach images. When they cannot be attached (not signed in, no access, no way to
upload), the pull request still goes out without them, and Needs attention says so with the screenshot files.

After the push, wait for the pull request's CI checks: `gh pr checks --watch` in the background with a completion
notification, up to 30 minutes, and end the turn while it runs. A failure the change caused is fixed like any
failing check: fix, verify, commit, push, wait again. A failure also on the base branch is baseline. Checks still
running after the wait are ⏳ in the report.

## Early draft (when asked)

When the user asks for a draft pull request on the first commit, early or first, do not wait for this step. As
soon as the first verify passes its completion gate (step 7), commit as above, push, and open the draft with the
body above, its verification marked in progress. Then QA and the review go on as usual; each later round is a
new commit pushed to the same branch. Here, the last commit is pushed and the body updated to the final state. A
push or draft that fails early is noted and tried again here; it never holds the flow.

Asked later in the run, it opens at once when nothing runs in the worktree, otherwise as soon as what runs there
ends (a build round once its verify passes), before the next thing starts. Never pause, stop or signal a running
profile for it, and never park it on a command that was blocked: say when in the reply,
`⏳ 11/11 Report · draft pull request after this round's verify · by <clock time>`, and open it then.

## Merge (only when asked)

When the user asked for a merge, with or without a condition ("merge it if there are no regressions"), merge
each delivery once these hold:
- every check is pass or baseline on the committed tree;
- no new failing test since the baseline, and every must-not-change item has its proof;
- no open **act** finding and no failing QA item;
- the pull request's CI is pass or baseline;
- every condition the user named.

"No new failing test" is the last verify's by-name comparison with the baseline on the committed tree. When the
base branch moved since, update the delivery branch on it the project's way (merge or rebase), run the last
verify once more on that tree, and compare by name again. A conflict goes to that delivery's builder as a
follow-up (without a build profile, you resolve it), keeping what both sides meant; that verify is its proof,
with no review. Several deliveries merge one after another, each
updated on what the previous merge left. That is the whole proof: no extra suites on other trees, harnesses or
environment patches, and a failure that passes alone is flaky, never a regression (`steps/verify.md`). Never
run the full suite for a merge: CI runs it, and a check the run does not run never holds a merge.

Mark the draft ready (`gh pr ready`). That can start the CI checks again, so wait for them on the head commit as
after a push (Pull request), and merge only when they pass or are baseline, with the method the repository's recent
merges used (`gh pr merge --squash`, `--merge` or `--rebase`). Then confirm on the remote that it is merged. A
condition that fails is work, not a stop: fix it within the limits, then merge. Only a regression still open after
the limits keeps a delivery unmerged, and the status line names it with what was tried.

## Finish before reporting

The report comes after the work is finished, not when the steps have run once. Before writing it, check that
every step in the report's Proof below would be ✅ or —. For any other step, go back and finish it while the
limits in `cpipe:run` allow: fix every **act** finding and QA failure, retry a check another way,
wait for CI, merge when asked. A step stays open only when the run truly cannot close it: something only the user
may do, a check that needs what this machine does not have, or a limit reached. A question about how the code
should behave, a needed policy, a role's hold or blocked verdict, a builder saying a fix is out of scope
(`steps/review.md`, Fix and the delta review), or a flaky result whose cause is unknown
(`steps/verify.md`, New failures) never keeps a step open. Decide it from the
user's instructions (`cpipe:run`, The user's instructions), fix and verify. Then check each instruction in
`now.md`: every one is met, or has its reason from that list. Test it is the last thing left for the user, and
only when needed.

## Running app

When the change has a page or an endpoint a person can use, leave it running for them: start the app from the
worktree the way the project does (its dev script, the same setup QA used) in the background without a
completion notification, wait until it answers, and put the URL in Test it. For a command, give it ready to run.
One app per delivery: stop the one this run started before (an earlier report, a follow-up) first, and use
another port if the default is taken. If it will not start, say so in Test it with the error; never ask the user
to start it. A follow-up restarts it with the new code.

## Base worktree

Once no later delivery of this run uses it, remove the base worktree set up added (`now.md`): stop what still
runs inside it (`lsof -d cwd -Fn`), then `git worktree remove --force <path>` and `git worktree prune`. A failed
removal is noted in the run log and never holds the report.

## Report

Post one message in the chat, in this order. With several deliveries, one status line per delivery first, then
this report for each under a heading naming its issue.

Short, and still complete: the user reads it in a minute and knows what changed, what was proven and what is
left for them. What needs the user comes first; what went as planned is a count, not a row. Plain words,
technical terms where they are precise; file and function names where they help. One line per row, cells of a
few words; no paragraph longer than three lines; no line that repeats another section; omit a section with
nothing in it. Facts only, from the run log. Name the step or role (Review, QA), never a profile; profiles,
skills and tools are named only in Needs attention, Cost and Details. Any other internal name gets a plain
description.

```
<status line>

<Summary>

## Test it
App: <URL or command, worktree>
| # | What to check | How | Expected |

## Before and after
| # | What | Before | After | QA |

## Needs attention
| What | Why it matters | Recommended |

## Proof
<one line per kind of proof>

## Where to look
| Where | Why |

Risk: <blast radius> · Rollback: <how> · Confidence: <what it rests on; the weak spot>

## Approach
| Choice | Why | Instead of |

## Run graph
<the flow chart>

Cost: <minutes> · <tokens> (<cached share>) · <who did each step>
Details: <one line>
```

**Status line**, one of:
- `✅ Done: <goal>`: every step is ✅ or —, and no manual testing is needed.
- `✅ Done, test by hand: <goal>`: the same, plus the Test it items.
- `⚠️ Done except <what>: <goal>`: steps that stay open (something only the user may do, an open security
  finding, a limit reached). Name each, and each unmet instruction with its reason.
- For a review: `✅ Approved: <change>` or `⚠️ Changes needed: <what>`.

**Summary**, by kind, two to four lines with no heading:
- **bug:** **What happened**: the symptom and where the user saw it. **Cause**: the root cause, with the file and
  the mechanism (a stale closure, a race between two writes, a missing permission check), and the commit and
  pull request that introduced it, with how sure that is (`playbooks/bug.md`). **Fix**: what changed
  and why it removes the cause, not only the symptom.
- **feature:** **Added**: what the user or system can do now. **How**: the main pieces and where they live.
- **refactor, chore, port or improvement:** **Before**, then **After**, then **Why**.
- **review:** what the change does, then the **Verdict** with its reason.

**Test it**: the running app (Running app above) and only what this machine could not verify (a signed-in
session, a device, a live service, visual judgment), each with exact steps and the expected result, so the user
can run it in a few minutes. Every QA gap is in it, so nothing untested passes silently: each QA item that is
**not run here** or still fails after the last round, each screenshot pair QA could not judge, and QA that was
skipped or cut short (no QA profile answered, the app would not start, a limit reached), each with what was not
checked and why. Never a code review, a re-review, reading a diff, running a check or command, or confirming a
result: the run does those. When nothing is left, the section is one line: `None needed: <why>`, with the app
line when one is running.

**Before and after**: what the user would see change, so they can compare it in the chat without opening the
app. One row per QA pair (`steps/qa.md`): the page, state or command; before and after; QA's pass, fail or not
run here. For a UI, the before and after screenshots, attached the way this chat's instructions say a local file
or image is shown, so they render inline; where the chat has no way to show files, their paths. For an endpoint,
CLI or output change, the before and after text in short code blocks (the response, output or message), trimmed
to the lines that changed. Pairs QA could not judge come first. Omit for a change nobody sees.

**Needs attention**: everything that is not simply done, one row each, most serious first:
- a role whose profile failed, or a tool or service that blocked a step (each `Problem:` line in the run log,
  `roles.md`, When a role fails), so the user knows
  who did not do their part: **What** names the role, its profile and the exact error in a few words
  (`Review: sentry, provider startup failed twice`); **Why it matters** what it cost (`review done in this chat,
  not independent`); **Recommended** what clears it next time (the provider, the profile's config);
- every step that ran without a sign-in or credential it needed (QA behind a login, screenshots for the pull
  request, a live service), every run it happens: **What** names the service and what was missing
  (`QA: no signed-in GitHub session`); **Why it matters** what was skipped; **Recommended** the exact command
  or setting that provides it;
- an open finding, a baseline failure that stays, an unmet instruction, a step left ⚠️, ⏳ or ❌;
- **consider** and **noted** findings, out-of-scope problems and known limits, and a follow-up delivery (a
  hotfix's root cause), with what you recommend and why; never only "unresolved".

Findings that were fixed are not rows here: Proof counts them. Omit the section when nothing is left.

**Proof**: what the run proved, one line per kind, numbers over words, `✅` or `⚠️` first:
- **Tests**: the bug reproduced; new tests RED → GREEN, how many.
- **Checks**: each check and its state (lint, types, the related tests with pass and baseline counts, the
  build; the full suite: CI).
- **QA**: scenarios run on the real surface and how many passed; must-not-change items held.
- **Review**: findings by outcome (fixed, noted, dismissed), with the fixed ones named in a few words each, and
  the rounds run.
- **Steps**: one line for the rest of the eleven steps: `✅ 1–11` with each — or non-✅ step and its reason
  (`— 3 Design: no UI change`).

**Where to look**: the two or three places in the diff a human reviewer should read first, as `file:line` (a
link when the chat renders one) and why each matters: the core of the fix, a changed contract or shared function,
the line the review argued about, the code QA could not reach. Taken from the plan's flow map and the review.
Omit for a change of a few lines.

**Risk**, one line under it: **blast radius**, what else reaches the changed code (callers, routes, jobs,
consumers, from the flow map: `3 routes, 1 nightly job`); **Rollback**, how to undo it (`revert the commit`, or
`⚠️ the migration cannot be undone: <why>`); **Confidence**, what the result rests on (`6 tests and QA on the
running app`) and its weakest spot (`the export path is covered by tests only`), and the past record
(`cpipe:run`, Learning, Confidence).

**Approach**: the choices a reviewer would question and every assumption made for an open question, at most
four rows: **Choice** the approach or assumption; **Why** the deciding fact (from the code, the request or a
lesson); **Instead of** the alternative not taken and the problem it would have caused. Omit when the change had
only one sensible way.

**Run graph**: what happened in the whole run, drawn (Run graph below): a structured view when the chat offers
one, else the flow chart.

**Cost**: one line, so the user sees what the delivery spent: the minutes from the run log's first to last row;
the tokens of every session that worked in this delivery's worktree since the run started, with the cached share,
read-only from each profile's session store (`<Hermes root>/state.db` and `<Hermes root>/profiles/*/state.db`,
table `sessions`: `input_tokens`, `output_tokens`, `cache_read_tokens` where `cwd` is in the worktree and
`started_at` is after the run began); who did each step (`plan archon · build forge · review sentry · QA this
chat`). A number that cannot be read is left out, never guessed.

**Details**: one line: the branch, the commit SHA and subject, the pull request link, `git diff --stat` in a few
words, the user skills used, the learnings written, and `run-graph.html`.

The report never asks the user to fix, install, run, review or verify anything you could have done yourself, and
has no "next steps" or "remaining" list for the user beyond Test it and Needs attention. If you could do it, do it
before reporting.

## Run graph

Draw the run from the run log's times and the times of the files in `logs/`; a time in neither is left out,
never guessed. Two Mermaid diagrams:

- **Flow** (`flowchart LR`): one node per step and per round (`Build 2`, `Delta review`), labelled with the step,
  the role and its profile, the minutes and the result in a few words. Arrows in the order things happened; work
  that ran in parallel branches from the same node, and a fix round is an arrow back. Retries, interruptions, a
  wait for the user and any gap over 10 minutes are dashed arrows labelled with the cause, into a node of class
  `bad`; verified results are class `ok`. At most 25 nodes: fold repeated rounds into one. Colors that read in
  light and dark: `classDef bad fill:#e5484d33,stroke:#e5484d` and `classDef ok fill:#30a46c33,stroke:#30a46c`.
- **Timeline** (`gantt`, `dateFormat HH:mm`): one section per role and this chat, a bar per piece of work, and
  each gap or stall as a `crit` bar named by its cause.

Write them to `<run dir>/graph.part` as two blocks, `<script type="text/x-mermaid" data-title="Flow">` and
`data-title="Timeline"`, after a `<p>` line with the goal, branch and date. Then
`cat <this skill's folder>/run-graph.html <run dir>/graph.part > <run dir>/run-graph.html`: the template (beside
`SKILL.md`) renders them in a browser. Details names `run-graph.html`.

In the report, show the same flow as `graph.part`, never a shorter one:

- **A structured view, when the chat offers one.** Instructions anywhere in this chat (the system prompt, or a
  note attached to the user's messages) that describe a fenced format for interactive or structured views with a
  graph, workflow or timeline block: the run graph always uses it, never a code block, whatever they say about
  when to prefer plain text. The flow becomes the graph block (each node titled with the step, the role and its
  profile; its detail the minutes and the result; each edge labelled, retries and interruptions included), the
  steps a workflow block with their states and evidence from the run log, and the run graph's text the
  fallback. Where the graph block takes a node status or an edge style, the nodes the flow marks `ok` are
  passed and those it marks `bad` failed or blocked, and the dashed arrows are dashed edges. Follow the format
  exactly as that chat describes it. Never paste `run-graph.html` into a view that
  runs offline: its diagrams load a remote script.
- **Otherwise** the flow in a ` ```mermaid ` block.

## Rules

- Commit only the plan's scope: check `git status` and the staged diff before committing; never `git add` files
  you did not mean to change.
- Never push, open a pull request, merge or message anyone unless the user asked for it in this work; when they
  did, do it (Merge).
- Never claim a state the run log does not show.
- Never write a pull request or commit message by hand when the user has a skill for it.
- No secrets, local paths or chat details in the commit message or pull request.

## Done when

The work is committed on the delivery branch, the run log has the final row, `run-graph.html` is written, every
`Problem:` line in the run log has its Needs attention row, and the report is posted in this file's sections and
order, never as free text.
