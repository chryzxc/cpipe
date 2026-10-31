# cpipe: commit, pull request and merge

Step 11 of `cpipe:run`, loaded before the commit, and again for an early draft
(Early draft) or a merge. The report itself is `steps/report.md`.

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
base, one per delivery (`cpipe:run`, One issue, one delivery). Title in the commit style. Body: any verified GitHub
issue it resolves in the project's linking style (`Closes #123`), what changed and why, how it was verified (each check with its state),
anything not run here and how to check it, open findings. No local paths, internal tool names or chat details.
Never merge, mark ready or request reviewers unless asked. If the push or the pull request fails, report the
error and the exact command to retry; the work stays committed locally.

Use `Closes`, `Fixes` or `Resolves` only for a GitHub issue whose repository and number were explicitly provided
as an issue or independently verified against the requested work. Never infer an issue number from a task, backlog,
card, lane or audit identifier. If no matching issue is verified, omit the closing reference; never invent or
substitute one. After publishing or editing, read back the body and inspect its closing keywords against those
verified issues, even if `closingIssuesReferences` is empty on a non-default base. A syntax linter cannot verify
issue identity; correct unintended references when authorized.

For a visible change, put QA's before and after screenshots in the pull request the way the user's
pull-request skill says to attach images. When they cannot be attached (not signed in, no access, no way to
upload), the pull request still goes out without them, and Needs attention says so with the screenshot files.

After the push, wait for the pull request's CI checks: `gh pr checks --watch` in the background with a completion
notification, up to 30 minutes, and end the turn while it runs. A failure the change caused is fixed like any
failing check: fix, verify, commit, push, wait again. Save the checks to `results/ci.json`
(`gh pr checks --json name,bucket`) and the base
commit's to `results/ci-base.json`
(`gh api repos/<owner>/<repo>/commits/<base sha>/check-runs --jq '[.check_runs[]|{name,conclusion,status}]'`):
`gate` counts a job that fails on both as the base's. A skipped job (`skipping`) is the workflow's own choice, not
a failure. A job that fails before any of its steps runs (billing, no runner) fails on the base too, so it is the
base's. Neither holds a merge; both go under Risk. Checks still running after the wait are ⏳ in the report.

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
each delivery once these hold, each one an item in `track.json` (`cpipe:run`, Facts) and `gate` says `PASS`:
- every check is pass or baseline on the committed tree (`compare` for each check: no new name, no rerun left);
- every must-not-change item has its proof;
- no open **act** finding and no failing QA item;
- the pull request's CI is pass, skipped or baseline (`results/ci.json` and `results/ci-base.json`, Pull request);
- every condition the user named (`now.md`, Instructions).

These are the whole condition. Nothing else holds a merge the user asked for: not a check that was not run here
(the full suite, a typecheck tool that is missing), a CI job that was skipped, a flaky result whose cause is unknown, a deployment or
production setup that was not verified, a reviewer's or a skill's general readiness gates, or wanting more
proof. Those go under Risk in the report. When `gate` says `PASS`, merge in the same turn: no extra review,
comparison or readiness pass first. `FIX` is work, `WAIT` a wait line, `ASK` the ❓ waiting lines,
`NEEDS-USER` the `⚠️ Done except` line.

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
condition that fails is work, not a stop: fix it, verify, and check again, round after round (`cpipe:run`,
Limits), then merge. A report that says "not merged" while every condition above holds is a mistake: merge.

Updating a pushed branch: merge the base into it unless the project rebases; after a rebase, push with
`--force-with-lease`, and only to this delivery's own branch, never one someone else pushed to. A merge the
repository's rules refuse until a person acts (a required approving review, a code owner) is never forced:
no `--admin` or other bypass. Turn on auto-merge when the repository allows it (`gh pr merge --auto` with the
method), and report `⚠️ Done except` that approval.

## Rules

- Commit only the plan's scope: check `git status` and the staged diff before committing; never `git add` files
  you did not mean to change.
- Never push, open a pull request, merge or message anyone unless the user asked for it in this work; when they
  did, do it (Merge).
- Never write a pull request or commit message by hand when the user has a skill for it.
- No secrets, local paths or chat details in the commit message or pull request.
