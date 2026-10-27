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
`⏳ draft pull request after this round's verify, by <clock time> at the latest`, and open it then.

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
environment patches, and a failure that passes alone is flaky, never a regression (`steps/verify.md`).

Mark the draft ready (`gh pr ready`). That can start the CI checks again, so wait for them on the head commit as
after a push (Pull request), and merge only when they pass or are baseline, with the method the repository's recent
merges used (`gh pr merge --squash`, `--merge` or `--rebase`). Then confirm on the remote that it is merged. A
condition that fails is work, not a stop: fix it within the limits, then merge. Only a regression still open after
the limits keeps a delivery unmerged, and the status line names it with what was tried.

## Finish before reporting

The report comes after the work is finished, not when the steps have run once. Before writing it, check that
every row of the Steps table below would be ✅ or —. For any other row, go back and finish it while the limits
in `cpipe:run` allow: fix in-scope findings and QA failures, retry a check another way,
wait for CI, merge when asked. A row stays open only when the run truly cannot close it: something only the user
may do, a check that needs what this machine does not have, or a limit reached. A question about how the code
should behave, a needed policy, a role's hold or blocked verdict, or a flaky result whose cause is unknown
(`steps/verify.md`, New failures) never keeps a row open. Decide it from the
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

Post one message in the chat, in this order. With several
deliveries, one status line per delivery first, then this report for each under a heading naming its issue.

Short and complete: plain words, technical terms where they are precise; one line per table row, cells of a few
words; no paragraph longer than three lines; omit a section with nothing in it, apart from Steps. Facts only,
from the run log. Tables name the step or role (Review, QA), never a profile; profiles, skills and tools are
named only in Run problems and Details. Any other internal name gets a plain description.

```
<status line>

## Summary
<the recap for the kind, below>

## Steps
| # | Step | Status | Result |

## Run graph
<the flow chart>

## Run problems
| What failed | Error | Tried | Instead | Effect |

## Findings
| Found by | Finding | Outcome |

## Approach
| Idea | Why | Avoided | Trade-off |

## Before and after
| # | What | Before | After | QA |

## Test it
App: <URL or command, worktree>
| # | What to check | How | Expected |

## Not in this change
- <one line each>

## Details
Branch · worktree · commit · pull request · files changed · roles · lessons
```

**Status line**, one of:
- `✅ Done: <goal>`: every Steps row is ✅ or —, and no manual testing is needed.
- `✅ Done, test by hand: <goal>`: the same, plus the Test it items.
- `⚠️ Done except <what>: <goal>`: rows that stay open (something only the user may do, an open security
  finding, a limit reached). Name each, and each unmet instruction with its reason.
- For a review: `✅ Approved: <change>` or `⚠️ Changes needed: <what>`.

**Summary**, by kind. Three to six lines, file and function names where they help:
- **bug:** **What happened**: the symptom and where the user saw it. **Cause**: the root cause, with the file and
  the mechanism (for example, a stale closure, a race between two writes, a missing permission check). **Fix**:
  what changed and why it removes the cause, not only the symptom.
- **feature:** **Added**: what the user or system can do now. **How**: the main pieces and where they live.
- **refactor, chore, port or improvement:** **Before**, then **After**, then **Why**.
- **review:** what the change does, **Before** and **After**, then the **Verdict** with its reason.

**Steps**: all eleven steps of the flow, in order, one row each. Status is ✅ done and verified, ⚠️ done with a
known limit (the Result says which), ⏳ waiting on something outside the run (CI still running after the wait),
❌ not done (why), or — not needed (why). Result is the proof in a few words, never "done":

| Step | Result shows |
|---|---|
| 1. Set up | the worktree, dependencies installed, worktrees tidied |
| 2. Explore | entrances, consumers and guards mapped; risks found; the baseline (failing count, or green) |
| 3. Design | the states and copy settled, or why there was nothing user-facing |
| 4. Decide | how many assumptions (each in Approach) |
| 5. Plan | how many build steps; the plan gate passed |
| 6. Build | the bug reproduced; new tests RED → GREEN, how many |
| 7. Verify | each check and its state: pass, fail, baseline (same failing names as before), not run here |
| 8. QA | scenarios run on the real surface and how many passed; must-not-change items held |
| 9. Review | findings by outcome (fixed, noted, dismissed); the rounds run; fixes after the last review |
| 10. Clean | what the cleanup removed; the last verify on the final tree |
| 11. Commit | the SHA and subject; the pull request link and CI result if one was asked for |

**Run problems**: each role whose profile failed and each tool or service that blocked a step, from the run log,
so the user knows who did not do their part and why. **What failed** names the role and its profile (`Review:
sentry`); **Error** is the exact error in a few words (`provider startup failed`); **Tried** the attempts
(`2 runs`); **Instead** who did the step (`this chat, fresh pass`); **Effect** what it costs the result (`review
not independent`) and what clears it next time (the provider, the profile's config). A Steps row whose step ran
this way is ⚠️ and points here. Omit only when nothing failed.

**Findings**: what the run found that the task did not say, and what became of it: review and QA findings, a
root cause deeper than the symptom, a risk from the premortem that proved real, a baseline failure, a missing
test. Found by is the step or role. Outcome is fixed (how), noted, dismissed (why) or open with what you
recommend and why; never only "unresolved". Omit when nothing was found.

**Approach**: the ideas the change is built on and every assumption made for an open question, so a reviewer
sees the reasoning without reading the diff. One row per idea: **Idea** is the approach, pattern or library
chosen; **Why** the deciding fact (from the code, the request or a lesson); **Avoided** the alternative not taken
and the problem it would have caused (a regression, a race, a breaking contract, a wider diff); **Trade-off**
what this choice costs or leaves open, or `none`. Omit when the change had only one sensible way.

**Before and after**: what the user would see change, so they can compare it in the chat without opening the
app. One row per QA pair (`steps/qa.md`): the page, state or command; before and after; QA's pass, fail or not
run here. For a UI, the before and after screenshots, attached the way this chat's instructions say a local file
or image is shown, so they render inline; where the chat has no way to show files, their paths. For an endpoint,
CLI or output change, the before and after text in short code blocks (the response, output or message), trimmed
to the lines that changed. Pairs QA could not judge come first. Omit for a change nobody sees.

**Test it**: the running app (Running app above) and only what this machine could not verify (a signed-in
session, a device, a live service, visual judgment), each with exact steps and the expected result, so the user
can run it in a few minutes. Every QA gap is in it, so nothing untested passes silently: each QA item that is
**not run here** or still fails after the last round, each screenshot pair QA could not judge, and QA that was
skipped or cut short (no QA profile answered, the app would not start, a limit reached), each with what was not
checked and why. Never a code review, a re-review, reading a diff, running a check or command, or
confirming a result: the run does those. When nothing is left: `None needed: <why>`, with the app line when one
is running.

**Not in this change**: out-of-scope problems, **consider** and **noted** findings not in Findings, known
limits, a follow-up delivery (a hotfix's root cause). One line each, or omit the section.

**Run graph**: what happened in the whole run, drawn (Run graph below): a structured view when the chat offers
one, else the flow chart.

**Details**: the branch and worktree, the commit SHA and subject, the pull request link, `git diff --stat` in one
line, who did each step (a profile or this chat) and any fallback, the user skills used, and the lessons written.

The report never asks the user to fix, install, run, review or verify anything you could have done yourself, and
has no "next steps" or "remaining" list for the user beyond Test it and what only the user may do. If you could
do it, do it before reporting.

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
`SKILL.md`) renders them in a browser. The Details line names `run-graph.html`.

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

The work is committed on the delivery branch, the run log has the final row, `run-graph.html` is written, and
the report is posted.
