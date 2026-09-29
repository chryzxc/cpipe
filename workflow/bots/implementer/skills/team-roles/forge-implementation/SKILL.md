---
name: forge-implementation
description: "Use when Forge delivers a feature or fix card, with or without an Archon plan."
version: 1.2.0
---

# Forge Implementation Procedure

## Scope
Deliver what the card's REQUEST asks, following the Archon plan when one is attached and planning it yourself when not. Preserve contracts, unrelated work, and authority boundaries. Do not self-approve, merge, deploy, or alter production systems.

## Procedure
1. Confirm the approved task/plan, exact write scope, repository instructions, frozen input state, acceptance criteria, and required gates.
2. Re-open the named evidence and plan. When paths, APIs, or dependencies differ from the plan, adapt within the REQUEST and record it under `OUT_OF_PLAN`; block only when the REQUEST itself is contradictory.
3. Follow the work packets in order. Use RED → minimal GREEN → REFACTOR for testable behavior.
4. Resolve implementation details yourself, including files, tests, and dependencies the REQUEST needs. Record every deviation.
5. Preserve validation, authorization, tenancy, errors, compatibility, transactions, idempotency, concurrency, and observability where applicable.
6. Pin first: before editing source, write every UNPINNED test from the plan asserting today's behavior, run it green on the unchanged code, and commit it. Then RED/GREEN the requested behavior. Run the tests related to every changed file (`jest --findRelatedTests`, `vitest related`, or the importing tests) in every package the change reaches, plus lint/typecheck, and report files changed, commands and observed results, deviations, residual risk, and next independent gate.
7. Push the branch and open or update its draft PR (body ends with `## How to test`). Freeze the result. For a material implementation with Sentry required, call `hermes kanban request-review <TASK_ID> --reviewer sentry --summary <evidence> --metadata <frozen-handoff-json>` with the frozen base/head, changed files, exact test results, and residual risk; do not call `hermes kanban complete` at the local-commit checkpoint. On returned changes, address only stable findings, rerun affected checks, freeze the new head, and request fresh review. Never create kanban cards. Request Cypher or Aegis only when their gate is triggered.

## Phase receipts and stop rules
Emit `PRECHECK`, `RED`, `IMPLEMENTING`, `GREEN`, `REGRESSION`, `COMMIT_READY`, or `BLOCKED` with elapsed time and the current command/result. Install missing dependencies yourself. Near the end of the runtime budget, commit and push what is green and hand off with the gap as `READY_WITH_RISK` rather than time out. A normal test gate that needs `--forceExit` is residual risk, not a stop.
When a terminal kanban call (`complete`, `request-review`, `request-changes`, `block`) is rejected, stop and comment `WIP_PRESERVED: <worktree>@<sha> +<n> files; verified: <commands>` on the card; never discard or reset the work. If a goal-mode judge errors, stop spending turns and block with `JUDGE_ERROR: <error excerpt>`. On the next run, PRECHECK accepts this card's own `WIP_PRESERVED` diff instead of blocking on a HEAD mismatch.

## Escalation
Block with 2-4 options only for a product decision, security policy, schema/migration risk, external side effect, or unsafe instruction the REQUEST does not settle. Everything else is yours to finish.

## Verification
Completion requires current test and runtime evidence for the assigned acceptance criteria. UI work requires fresh live visual evidence. Passing Forge tests do not replace Sentry frozen-diff review or any separately triggered Sentinel QA gate.
