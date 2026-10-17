# cpipe: the report step

Step 11 of `cpipe:run`.

Before writing the commit or the pull request, check your skill list for the user's own skill for commit
messages or pull-request content and load it. If one exists, it decides the title, body, template and any lint
script to run before `gh pr create` or `gh pr edit`; the sections below are the fallback.

## Commit

Commit on the delivery branch in the worktree. Use the project's commit convention; otherwise Conventional
Commits: `<type>(<scope>): <imperative summary>`, a truthful type, and a body that says what changed and why.
Only the scoped work, no AI attribution, no secrets. Follow the project's hooks; if a hook fails, fix the cause
rather than skipping it.

## Pull request (only when asked)

Only if the user asked for one in this task: push the branch and open a **draft** pull request against the plan's
base. Title in the commit style. Body: what changed and why, how it was verified (each check with its state),
anything not run here and how to check it, open findings. No local paths, internal tool names or chat details.
Never merge, mark ready or request reviewers unless asked. If the push or the pull request fails, report the
error and the exact command to retry; the work stays committed locally.

After the push, wait for the pull request's CI checks (`gh pr checks --watch`, or poll), up to 30 minutes. A
failure the change caused is fixed like any failing check: fix, verify, commit, push, wait again. A failure also
on the base branch is baseline. Checks still running after the wait are ⏳ in the report.

## Finish before reporting

The report comes after the work is finished, not when the steps have run once. Before writing it, check that
every row of the Work table below would be ✅ or —. For any other row, go back and finish it while the limits
in `cpipe:run` allow: fix in-scope findings and QA failures, review the last fix, retry a check another way,
wait for CI. A row stays open only when the run truly cannot close it: something only the user may do, a check
that needs what this machine does not have, or a limit reached. Manual testing is the last thing left for the
user, and only when needed.

## Report

One message in the chat, in this order. Plain words, technical terms where they are precise; short sentences;
no paragraph longer than three lines. Facts only, from the run log.

```
<status line>

## Summary
<the recap for the kind, below>

## Decisions
| Decision | Chosen | Instead of | Why |

## Work
| Item | Status | Proof |

## Manual testing
| # | What to check | How | Expected |

## Not in this change
- <one line each>

## Details
Branch · commit · pull request · files changed · roles · lessons
```

**Status line**, one of:
- `✅ Done: <goal>`: every Work row is ✅ or —, and no manual testing is needed.
- `✅ Done, test by hand: <goal>`: the same, plus the Manual testing items.
- `⚠️ Done except <what>: <goal>`: rows that stay open (something only the user may do, an open security
  finding, a limit reached). Name each.

**Summary**, by kind. Three to six lines, file and function names where they help:
- **bug:** **What happened**: the symptom and where the user saw it. **Cause**: the root cause, with the file and
  the mechanism (for example, a stale closure, a race between two writes, a missing permission check). **Fix**:
  what changed and why it removes the cause, not only the symptom.
- **feature:** **Added**: what the user or system can do now. **How**: the main pieces and where they live.
- **refactor, chore or improvement:** **Before**, then **After**, then **Why**.

**Decisions**: each choice a reviewer might question: the approach picked over another, a library or pattern,
a trade-off, and every assumption made for an open question. One row each; the Why names the deciding fact. Omit
the section when nothing was chosen.

**Work**: one row per item, in flow order. Status is ✅ done and verified, ⚠️ done with a known limit (the Proof
says which), ⏳ waiting on something outside the run (CI still running after the wait), ❌ not done (why), or —
not needed (why). Proof is the evidence in a few words: test names, counts, the command.

| Item | Covers |
|---|---|
| Reproduced (bug) | the failing test or script before the fix |
| Implementation | the change, matching the plan's scope |
| New tests | how many, each failing before and passing after (RED → GREEN) |
| Focused tests, lint, typecheck, build | each with its result |
| Full suite | passed and failed counts; failures that are also on the base are marked baseline with their count |
| Flow map and must-not-change | each entrance, consumer and guard still working |
| Risks | the premortem risks covered |
| Design | states and copy, or why it was not needed |
| QA | scenarios run on the real surface and their result |
| Review, security review | findings fixed, noted, dismissed; the last fix reviewed |
| Cleanup | the cleanup pass and the checks after it |
| Commit, pull request, CI | the SHA; the link and CI result if a pull request was asked for |

**Manual testing**: only what this machine could not verify (a signed-in session, a device, a live service,
visual judgment), each with exact steps and the expected result, so the user can run it in a few minutes. When
nothing is left: `None needed: <why>`.

**Not in this change**: out-of-scope problems found, **consider** and **noted** findings, known limits. One
line each, or omit the section.

**Details**: the branch and worktree, the commit SHA and subject, the pull request link, `git diff --stat` in one
line, who did each step (a profile or this chat) and any fallback, and the lessons written.

The report never asks the user to fix, install or run anything you could have done yourself; if you could do it,
do it before reporting.

## Rules

- Commit only the plan's scope: check `git status` and the staged diff before committing; never `git add` files
  you did not mean to change.
- Never push, open a pull request, merge or message anyone unless the user asked for it in this task.
- Never claim a state the run log does not show.
- Never write a pull request or commit message by hand when the user has a skill for it.
- No secrets, local paths or chat details in the commit message or pull request.

## Done when

The work is committed on the delivery branch, the run log has the final row, and the report is posted.
