# cpipe: the review step

Step 9 of `cpipe:run`, at the same time as QA (`steps/qa.md`): start the reviewers in the background, then do
or hand over QA while they work. The builder never approves its own work: the review runs in a fresh context that did not
write the code.

## 1. Brief the reviewer

Use your subagent tool (`delegate_task` or equivalent) for one reviewer. Use two in parallel when the change
touches authentication, permissions, payments, data deletion or migrations; findings both report are high
confidence. If the roles file names a `review` (and, for those areas, a `security`) profile, the reviewer is
that profile instead (`roles.md` in `cpipe:run`).

A review always happens. If the profile fails or runs out of time, use a subagent; if that fails too or you have
no subagent tool, start a fresh review yourself: read only the brief below and the diff, not your earlier
reasoning, and note in the run log that the review was not independent. Never leave a review for the user.

Give the reviewer only what it needs, not your reasoning:

```
Review this change. You did not write it. Read the code; do not edit anything.

Goal: <the plan's goal, one sentence>
User's instructions: <each one from now.md, word for word; they settle the questions they answer>
Acceptance: <the plan's acceptance criteria>
Repository: <worktree path>   Diff: git diff <base>...HEAD
Checks run: <each check with its state from the run log>
Flow map and risks: <entrances, exits, guards, behavior table with its After column, must-not-change list and
premortem from the plan>

Review against:
- Correctness: does it meet each acceptance criterion? Logic errors, edge cases, error paths.
- Behavior: before reading the plan's table, build your own from the base. Read the base version of each changed
  file (`git show <base>:<path>`) and the code on the other side of each boundary it crosses (the client of a
  server change, the server of a client change). For each actor and case (each role, the owner and a stranger,
  signed out, a job or another service, another client; new and older stored data), write what it gets before
  and after. Report each difference the goal and the user's instructions do not ask for. Report each rule the
  diff adds or moves whose conditions differ from the side that held it before: a role, an ownership case, a
  state, a flag or a data shape that was allowed there and is not now.
- Proof: does each new test fail when the code it covers breaks, and assert the real behavior? Flag tests that
  catch no realistic break (duplicates, constants, getters, the framework, their own mocks, loose assertions) for
  removal, and real breaks no test catches.
- Regressions: do not trust the flow map. Search the whole repository for every usage of what the diff changed
  (functions, types, fields, routes, events, config) and list any entrance or consumer the map missed; open only
  the lines around each hit, not whole files. For each one, and for each must-not-change item, does it still
  behave the same? Follow changes across boundaries
  (server and client, services, packages, stored data).
- Missed risks: before reading the plan's risks, write your own premortem of how this diff could break in use
  (contracts, data and state, roles and environments, failure paths, user-facing states, cost, anything else).
  Then report each risk the plan did not cover, or covered without a real check.
- Security: injection, access control, secrets, unsafe input handling.
- Simplicity: needless abstraction, duplicated helpers, dead code, comments that restate code.

For each finding: file and line, the problem, why it matters, a concrete fix, severity (critical, major, minor),
and whether it is security-related. Say plainly if you found nothing that needs changing. Give findings, not a
verdict: no hold, block or release decision. The delivery run decides.
```

## 2. Sort the findings

Read every finding against the code yourself, then give each one a disposition and record it in the run log:

| Disposition | Means | Then |
|---|---|---|
| **act** | Real and inside the scope | Fix it now. |
| **consider** | Real, but a judgment call or outside the scope | List it in the report. |
| **noted** | Minor or stylistic | List it in the report. |
| **dismissed** | Wrong: the code already handles it | Write the evidence (file, line, test) in the run log. |

A path, behavior row or risk the reviewer found that the plan missed is added to the plan with its check; a
regression on it is **act**, even if the code sits outside the scope's files. Write a lesson for it (`cpipe:run`,
Lessons).

A finding the user's instructions decide is **act**, fixed the way they say. Any difference from today's behavior
that the goal and the user's instructions do not ask for is a regression and **act**. A check this change adds
that rejects something the user did not ask to block, and that works today, is one: narrow the check so the
existing behavior keeps working. That is not a security weakening, because the code is still stricter than before
the change. Record what the check now allows and why. A verdict from a reviewer, QA or a role (hold, blocked, not
ready for release) is not a finding. Take the findings behind it, sort them here, and carry on.

Nothing is dropped silently. A security finding is never dismissed without evidence, and an open security finding
you cannot fix inside the scope leads the report.

A finding that asks for evidence this machine cannot produce (a browser run, a device, a live service): do what
you can locally, then list it as **consider** with what is left for the user to check. Do not stop for it.

## 3. Fix and re-review

When both the review and QA are back, fix the **act** findings and the QA failures in one round, run
`steps/verify.md`, then one more review round alongside a QA re-run of the failed items. Round two is delta-only: give the
reviewer the diff since the first review and the list of earlier findings to confirm as resolved.

After two rounds, stop reviewing. Anything still open goes into the report with its disposition. If the second
round led to another fix, have that fix alone reviewed once more (the diff of the fix, the finding it answers), so
no change reaches the report unreviewed. Then go to the
cleanup step (`steps/build.md`, step 10).

## Rules

- The reviewer reads; it never edits the code. You make every fix.
- Brief the reviewer with facts (goal, acceptance, diff, check states), not your reasoning or your verdict.
- Never dismiss a finding without evidence in the run log, and never dismiss a security finding by judgment
  alone.
- Never widen the scope to satisfy a reviewer: an out-of-scope finding is **consider**. A regression the change
  caused is never out of scope.
- Two rounds at most, plus a review of a fix made after the last round.
- Every review gap is closed by the run, never handed to the user.

## Done when

Every finding has a disposition in the run log, every **act** finding is fixed and verified, and the review is
clean or its open items are recorded for the report.
