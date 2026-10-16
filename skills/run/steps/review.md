# cpipe: the review step

Step 9 of `cpipe:run`. The builder never approves its own work: the review runs in a fresh context that did not
write the code.

## 1. Brief the reviewer

Use your subagent tool (`delegate_task` or equivalent) for one reviewer. Use two in parallel when the change
touches authentication, permissions, payments, data deletion or migrations; findings both report are high
confidence. If the roles file names a `review` (and, for those areas, a `security`) profile, the reviewer is
that profile instead (`roles.md` in `cpipe:run`).

Give the reviewer only what it needs, not your reasoning:

```
Review this change. You did not write it. Read the code; do not edit anything.

Goal: <the plan's goal, one sentence>
Acceptance: <the plan's acceptance criteria>
Repository: <worktree path>   Diff: git diff <base>...HEAD
Checks run: <each check with its state from the run log>
Flow map: <entrances, exits, guards and the must-not-change list from the plan>

Review against:
- Correctness: does it meet each acceptance criterion? Logic errors, edge cases, error paths.
- Proof: do the tests fail without the change and assert the real behavior?
- Regressions: do not trust the flow map. Search the whole repository for every usage of what the diff changed
  (functions, types, fields, routes, events, config) and list any entrance or consumer the map missed. For each
  one, and for each must-not-change item, does it still behave the same? Follow changes across the boundary: a
  server change to the client that calls it, and the reverse.
- Permissions: for each role, permission level and tenant on the path, can it still do exactly what it could,
  no more and no less, on the server and in what the client shows?
- Security: injection, access control, secrets, unsafe input handling.
- Simplicity: needless abstraction, duplicated helpers, dead code, comments that restate code.

For each finding: file and line, the problem, why it matters, a concrete fix, severity (critical, major, minor),
and whether it is security-related. Say plainly if you found nothing that needs changing.
```

## 2. Sort the findings

Read every finding against the code yourself, then give each one a disposition and record it in the run log:

| Disposition | Means | Then |
|---|---|---|
| **act** | Real and inside the scope | Fix it now. |
| **consider** | Real, but a judgment call or outside the scope | List it in the report. |
| **noted** | Minor or stylistic | List it in the report. |
| **dismissed** | Wrong: the code already handles it | Write the evidence (file, line, test) in the run log. |

A path the reviewer found that the flow map missed is added to the map in the run log, and its regression
check is added to the plan; a regression on it is **act**, even if the code sits outside the scope's files.

Nothing is dropped silently. A security finding is never dismissed without evidence, and an open security finding
you cannot fix inside the scope leads the report.

A finding that asks for evidence this machine cannot produce (a browser run, a device, a live service): do what
you can locally, then list it as **consider** with what is left for the user to check. Do not stop for it.

## 3. Fix and re-review

Fix the **act** findings, run `steps/verify.md`, then one more review round. Round two is delta-only: give the
reviewer the diff since the first review and the list of earlier findings to confirm as resolved.

After two rounds, stop reviewing. Anything still open goes into the report with its disposition. Then go to the
cleanup step (`steps/build.md`, step 10).

## Rules

- The reviewer reads; it never edits the code. You make every fix.
- Brief the reviewer with facts (goal, acceptance, diff, check states), not your reasoning or your verdict.
- Never dismiss a finding without evidence in the run log, and never dismiss a security finding by judgment
  alone.
- Never widen the scope to satisfy a reviewer: an out-of-scope finding is **consider**. A regression the change
  caused is never out of scope.
- Two rounds at most.

## Done when

Every finding has a disposition in the run log, every **act** finding is fixed and verified, and the review is
clean or its open items are recorded for the report.
