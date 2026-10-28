# cpipe: the review step

Step 9 of `cpipe:run`, started in the background as soon as the first verify's narrow tests and completion gate
pass, while the related tests and the build run; QA (`steps/qa.md`) joins once those finish. The builder never
approves its own work: the review runs in a context that did not write the code. A run has one full review and at
most one delta review (section 3).

## 1. Brief the reviewer

The reviewer is the `review` profile from the roles file (`roles.md` in `cpipe:run`). When the change touches
authentication, permissions, payments, data deletion or migrations, the `security` profile reviews too, in
parallel, with the security brief below: it does not repeat the full review. Findings both report are high
confidence. Never hand a review to a subagent.

A review always happens. If a profile fails or runs out of time, run it once more. Without a review profile it runs
on this chat's own profile and model in a session of its own (`roles.md`). If every session fails, start a fresh
review yourself: read only the brief below and the diff, not your earlier reasoning, and note in the run log that
the review was not independent, with each attempt's error (`roles.md`, When a role fails). Never leave a review for
the user.

Give the reviewer only what it needs, not your reasoning:

```
Review this change. You did not write it. Read the code; do not edit anything. The checks below already ran
on this tree: do not run the suite or the build again.

Goal: <the plan's goal, one sentence>
User's instructions: <each one from now.md, word for word; they settle the questions they answer>
Acceptance: <the plan's acceptance criteria>
Repository: <worktree path>   Diff: git diff <base>...HEAD (`--stat` first, then each file's diff once)
Checks run: <each check with its state from the run log>
Plan: <run dir>/plan.md (its flow map, behavior table with the After column, must-not-change list and
premortem; read them after your own table and premortem below)

Review against:
- Correctness: does it meet each acceptance criterion? Logic errors, edge cases, error paths: an error
  swallowed, caught too widely or passed up without its context; a failure the user meets as a crash or silence.
  For each changed signature, type, field or response shape: does every caller still compile and get what it
  expects?
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
- Symptom patches: for each fix, is it at the cause or at the symptom (`steps/build.md`, Root cause)? An
  **act** finding when it special-cases the failing input or test data; edits or loosens a test so it passes;
  adds a retry, sleep, longer timeout or broad catch around the failure; guards one caller while its siblings
  share the path (search them); or silences a type or lint error without fixing its cause. Name the shared point
  where the fix belongs.
- Standard approach: an **act** finding for anything on the not-standard list of `steps/build.md` (Standard
  approach), or for a solution that departs from how this codebase, or the framework's documentation for the
  installed version, does the same thing, without a reason in `plan.md`. Name the standard way to use instead.
- Simplicity and readability: needless abstraction, duplicated helpers, dead code, comments that restate code,
  names that do not say what a thing is, unnamed meaningful literals, deep nesting, a function doing two jobs,
  a silenced type or lint error, and code that solves a problem differently from how this codebase already
  does (name the existing code to follow).

For each finding, in at most 5 lines: file and line, the problem, why it matters, a concrete fix, severity
(critical, major, minor), and whether it is security-related. Say plainly if you found nothing that needs
changing. Give findings, not a verdict: no hold, block or release decision. The delivery run decides.
```

The security reviewer gets the same brief up to `Plan:`, then only these items, so the two reviews never read
the same things twice:

```
Review against:
- Access: for each role, the owner and a stranger, signed out, and another tenant: what each can read, change
  or delete before and after (`git show <base>:<path>` for before), through every entrance in the plan's flow
  map, with forged ids, fields and query parameters. Each difference the goal does not ask for, and each guard
  the diff adds whose conditions differ from the side that held it before.
- Input and output: injection (queries, commands, paths, templates, HTML), unsafe parsing, open redirects, data
  of another user or tenant in a response, log or error message.
- Secrets and trust: credentials or tokens in code, logs or the client; checks only the client enforces.
- Concurrent writes: a check-then-write gap or a repeated request that gets past a guard.
```

## 2. Sort the findings

Give each finding a disposition from its text and record it in the run log. With a build profile you do not
read the code for this: every **act** finding goes to the builder, which checks it against the code before
fixing it; one it shows is not real (file and line, a test or a command's output) becomes **dismissed** with that
evidence. Without a build profile, read each finding against the code yourself.

| Disposition | Means | Then |
|---|---|---|
| **act** | Real, and the goal needs it fixed | Fix it now. |
| **consider** | Real, but a judgment call or a problem the goal does not cover | List it in the report. |
| **noted** | Minor or stylistic | List it in the report. |
| **dismissed** | Wrong: the code already handles it | Write the evidence (file, line, test) in the run log. |

The scope is the goal, not the files the plan listed. A finding that shows the change does not yet do what the
goal says is **act**, wherever its fix lands: a guard that can be bypassed, a race between the new check and the
write it protects, a path or caller the fix misses. Add the files and paths it needs to the plan. **consider** is
for problems the goal does not cover: pre-existing behavior the change leaves as it was, unrelated code.
A problem the old code already had, in the same form, is **consider** even right beside the change: a
check-then-write race the old code had on the same data, a hole in a path the goal does not name (a delete path
in a task about reads). The goal is met when the change does what it says and breaks nothing; hardening the
neighbourhood is a follow-up delivery, listed in the report. A delta review sorts its new findings the same way:
one is **act** only when the fix round caused it or the goal still is not met.

A path, behavior row or risk the reviewer found that the plan missed is added to the plan with its check; a
regression on it is **act**, even if the code sits outside the scope's files. Write a learning for it (`learning.md`
beside `cpipe:run`).

A finding the user's instructions decide is **act**, fixed the way they say. Any difference from today's behavior
that the goal and the user's instructions do not ask for is a regression and **act**. The change's own
description, plan or commit message is not the user asking: a feature it turns off or hides (a preview, an export,
a notification, a button) is a regression unless the user asked for that. A check this change adds
that rejects something the user did not ask to block, and that works today, is one: narrow the check so the
existing behavior keeps working, for every actor who can do it today, not only the one the finding names (a
repair path for an administrator does not give the action back to the users the check now blocks); the delta
review checks it against the base, actor by actor. That is not a security weakening, because the code is still
stricter than before
the change. Record what the check now allows and why. A verdict from a reviewer, QA or a role (hold, blocked, not
ready for release) is not a finding. Take the findings behind it, sort them here, and carry on.

Nothing is dropped silently. A security finding is never dismissed without evidence, and an open security finding
leads the report.

A finding that asks for evidence this machine cannot produce (a browser run, a device, a live service): do what
you can locally, then list it as **consider** with what is left for the user to check. Do not stop for it.

## 3. Fix and the delta review

When both the review and QA are back, the **act** findings and the QA failures are fixed in one round (with a
build profile, one builder follow-up), then `steps/verify.md`, then the delta review alongside a QA re-run of the
failed items. The delta review resumes the first reviewer's session (`roles.md`, Follow-ups) with the diff since
the first review (`git diff <the first review's tree id>`, never the whole diff again) and the earlier findings
to confirm as resolved. Nothing to act on means no delta review.

Brief every **act** finding as a fix at its cause (`steps/build.md`, Root cause), never as "fix it if it fits the
scope" or "investigate": the builder is not the one who decides it is out of scope. A builder that answers it cannot
be fixed in scope (the fix needs other files, other write paths, a shared lock or a conditional write) gets the same
finding back at once, with those paths added to the plan and the approach it named as the one to build. It stays
open only when the fix needs what this run cannot do: a change in another repository or service, a migration of live
data, access or credentials it does not have. Then it leads the report with what was tried and the fix it needs.

**When fixes fight.** A fix that breaks today's behavior, with no change found that keeps both after one more
approach: the working behavior wins, unless that fix is the goal itself. Restore the files of the last verify
that passed (its tree id: `git restore --source=<tree id> --worktree -- .`, and remove files added since), keep
the rest of the goal, and list the finding as **consider** with the change it needs (a migration, another
database setup, a shared lock across writers). The same when fixes chain: two delta findings in a row caused by
the previous fix mean the approach is too big; go back to the last green tree and take the narrower one. Before
calling a broken behavior a regression, check that it is real: a setup only a test fixture uses (a standalone
database, a local mode) is checked against how the project deploys and what CI runs (their config and docs). One
neither uses is a Risk line in the report, not a regression. A regression is measured against the current base:
after the base moved (an update onto it, a merge from it), read what the base now does on that path before
calling anything a regression. Behavior the base already has is not one; a test this change added that expects
otherwise is wrong, so fix the test to the base's behavior and say so in the report.

The delta review is the last full review round. Its **act** findings are fixed the same way and proven by their
tests and the checks (a race by a test that forces the interleaving), with no further review, except that a
security fix gets one more delta review from the security reviewer on that fix alone; the report lists them as
fixed after the last review. Anything else still
open goes into the report with its disposition. Then go to the last verify (`steps/build.md`, step 10).

Fixes for a failing check or a compile error, a conflict, the cleanup and an update onto a moved base get no
review: their tests and the checks are their proof.

## Rules

- The reviewer reads; it never edits the code. The builder (or you, without a build profile) makes every fix.
- Brief the reviewer with facts (goal, acceptance, diff, check states), not your reasoning or your verdict.
- Never dismiss a finding without evidence in the run log, and never dismiss a security finding by judgment
  alone.
- Never widen the goal to satisfy a reviewer: a problem the goal does not cover is **consider**. A regression
  the change caused, and a finding that the goal is not met yet, are never out of scope.
- An **act** finding is never left for the user to fix or to ask for again: no report goes out while one is
  open and the run can still fix it.
- Two review rounds at most (one full review, one delta review), plus the security delta above.
- Every review gap is closed by the run, never handed to the user.

## Done when

Every finding has a disposition in the run log, every **act** finding is fixed and verified, and the review is
clean or its open items are recorded for the report.
