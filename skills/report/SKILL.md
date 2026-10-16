---
name: report
description: "Step 11 of /cpipe: commit, PR if asked, the report."
version: 3.5.1
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delivery, commit, pull-request, report]
    related_skills: [cpipe:run, cpipe:verify]
---

# cpipe: report

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

## Report

One message in the chat. Lead with the headline:

- **Ready for your review:** every check passes, QA passes and the review is clean.
- **Ready for your review, with gaps:** the work is done, and some checks or QA items were not run here, or some
  findings are open. Name them in the headline.
- **Needs your decision:** the work is done except for things only the user may do (`cpipe:run`, Unattended)
  or an open security finding. List each with what you would do and why.

Always list the assumptions you made for open questions right after the headline, so the user can check them
first.

Then, briefly:

| | |
|---|---|
| Goal | the plan's goal |
| Branch | the branch and worktree path |
| Commit | short SHA and subject; the pull request link if one was opened |
| Changes | `git diff --stat` summary |
| Roles | who did each step (a profile or this chat), and any fallback |

- **Checks:** each with its state (pass, fail, baseline, not run here), and for "not run here" what was done
  instead.
- **Proof:** the RED then GREEN test names for a bug or feature; the baseline comparison for a refactor.
- **Design:** the note's states and copy, or why it was not needed.
- **QA:** what you exercised on the real surface and the result of each, or why it was not needed.
- **Review:** findings by disposition (act and fixed, consider, noted, dismissed with its evidence).
- **Assumptions** you made and **workarounds** you used.
- **For you to check:** anything only the user can verify (a browser flow, a live service), as a short list.

Facts only, from the run log. No essay. The report never asks the user to fix, install or run anything you
could have done yourself; if you could do it, do it before reporting.

## Rules

- Commit only the plan's scope: check `git status` and the staged diff before committing; never `git add` files
  you did not mean to change.
- Never push, open a pull request, merge or message anyone unless the user asked for it in this task.
- Never claim a state the run log does not show.
- Never write a pull request or commit message by hand when the user has a skill for it.
- No secrets, local paths or chat details in the commit message or pull request.

## Done when

The work is committed on the delivery branch, the run log has the final row, and the report is posted.
