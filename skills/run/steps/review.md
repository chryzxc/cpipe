# cpipe: the review step

Step 9 of `cpipe:run`, started in the background as soon as the first verify's narrow tests and completion gate
pass, while the full suite and the build run; QA (`steps/qa.md`) joins once those finish. The builder never approves
its own work: the review runs in a context that did not write the code. A run has one full review and at most one
delta review (section 3).

## 1. Brief the reviewer

The reviewer is the `review` profile from the roles file (`roles.md` in `cpipe:run`). When the change touches
authentication, permissions, payments, data deletion or migrations, the `security` profile reviews too, in
parallel; findings both report are high confidence. Never hand a review to a subagent.

A review always happens. If a profile fails or runs out of time, run it once more. If it fails again, or no
profile is set for the role, start a fresh review yourself: read only the brief below and the diff, not your
earlier reasoning, and note in the run log that the review was not independent, with each attempt's error
(`roles.md`, When a role fails). Never leave a review for the user.

Give the reviewer only what it needs, not your reasoning:

```
Review this change. You did not write it. Read the code; do not edit anything.

Goal: <the plan's goal, one sentence>
User's instructions: <each one from now.md, word for word; they settle the questions they answer>
Acceptance: <the plan's acceptance criteria>
Repository: <worktree path>   Diff: git diff <base>...HEAD
Checks run: <each check with its state from the run log>
Plan: <run dir>/plan.md (its flow map, behavior table with the After column, must-not-change list and
premortem; read them after your own table and premortem below)

Review against:
- Correctness: does it meet each acceptance criterion? Logic errors, edge cases, error paths.
- Behavior: before reading the plan's table, build your own from the base. Read the base version of each changed
  file (`git show <base>:<path>`) and the code on the other side of each boundary it crosses (the client of a
  server change, the server of a client change). For each actor and case (each role, the owner and a stranger,
  signed out, a job or another service, another client; new and older stored data), write what it gets before
  and after. Report each difference the goal and the user's instructions do not ask for. Report each rule the
  diff adds or moves whose conditions differ from the side that held it before: a role, an ownership case, a
  state, a flag or a data shape that was allowed there and is not now. For each line the diff removes or
  tightens, find why it was there (`git blame` on the base) and who relied on it.
- Proof: does each new test fail when the code it covers breaks, assert the real behavior, and reach the code
  the way the user does (from the screen, route or job, in the user's order, not internal calls)? Is every
  acceptance rule and every sequence in the plan's premortem (retry, cancel and reopen, another record) tested?
  Flag tests that
  catch no realistic break (duplicates, constants, getters, the framework, their own mocks, loose assertions) for
  removal, and real breaks no test catches.
- Regressions: do not trust the flow map. Search the whole repository for every usage of what the diff changed
  (functions, types, fields, routes, events, config) and list any entrance or consumer the map missed; open only
  the lines around each hit, not whole files. For each one, and for each must-not-change item, does it still
  behave the same? Follow changes across boundaries
  (server and client, services, packages, stored data).
- Missed risks: before reading the plan's risks, write your own premortem of how this diff could break in use
  (contracts, data and state, roles and environments, failure paths, concurrency, resources, performance,
  user-facing states, anything else).
  Then report each risk the plan did not cover, or covered without a real check.
- Security: access control for each role and for another user's records (forged ids, fields or query
  parameters), injection, secrets, unsafe input handling.
- Concurrency and resources: for each shared write, what two at once do (lost updates, duplicates, a
  check-then-write gap); for each connection, transaction, file, listener, timer or subscription the diff opens,
  where it closes on success and on error; anything that grows without a bound (caches, maps, queues, retries).
- Performance: queries or remote calls in loops, a new query without its index, unbounded results, blocking work
  on a hot path, repeated work that could run once, and how the cost grows with the data. Name the input size
  where it hurts; never flag a micro-optimization the data size does not justify.
- Simplicity: needless abstraction, duplicated helpers, dead code, comments that restate code.

For each finding, in at most 5 lines: file and line, the problem, why it matters, a concrete fix, severity
(critical, major, minor), and whether it is security-related. Say plainly if you found nothing that needs
changing. Give findings, not a verdict: no hold, block or release decision. The delivery run decides.
```

## 2. Sort the findings

Give each finding a disposition from its text and record it in the run log. With a build profile you do not
read the code for this: every **act** finding goes to the builder, which checks it against the code before
fixing it; one it shows is not real (file and line, a test or a command's output) becomes **dismissed** with that
evidence. Without a build profile, read each finding against the code yourself.

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

## 3. Fix and the delta review

When both the review and QA are back, the **act** findings and the QA failures are fixed in one round (with a
build profile, one builder follow-up), then `steps/verify.md`, then the delta review alongside a QA re-run of the
failed items. The delta review resumes the first reviewer's session (`roles.md`, Follow-ups) with the diff since
the first review and the earlier findings to confirm as resolved. Nothing to act on means no delta review.

The delta review is the last review round. Its **act** findings are fixed the same way and proven by their tests
and the checks, with no further review; the report lists them as fixed after the last review. Anything else still
open goes into the report with its disposition. Then go to the last verify (`steps/build.md`, step 10).

Fixes for a failing check or a compile error, a conflict, the cleanup and an update onto a moved base get no
review: their tests and the checks are their proof.

## Rules

- The reviewer reads; it never edits the code. The builder (or you, without a build profile) makes every fix.
- Brief the reviewer with facts (goal, acceptance, diff, check states), not your reasoning or your verdict.
- Never dismiss a finding without evidence in the run log, and never dismiss a security finding by judgment
  alone.
- Never widen the scope to satisfy a reviewer: an out-of-scope finding is **consider**. A regression the change
  caused is never out of scope.
- Two rounds at most: one full review and one delta review.
- Every review gap is closed by the run, never handed to the user.

## Done when

Every finding has a disposition in the run log, every **act** finding is fixed and verified, and the review is
clean or its open items are recorded for the report.
