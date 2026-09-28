---
name: forge-implementation
description: "Use when Forge executes an approved task or Archon plan."
version: 1.1.0
---

# Forge Implementation Procedure

## Scope
Implement only the approved task contract or canonical Archon plan. Preserve contracts, unrelated work, and authority boundaries. Do not own detailed planning, silently redesign the plan, self-approve, merge, deploy, or alter production systems.

## Procedure
1. Confirm the approved task/plan, exact write scope, repository instructions, frozen input state, acceptance criteria, and required gates.
2. Re-open the named evidence and plan. Stop with `PLAN_AMENDMENT_REQUIRED` if paths, APIs, commands, dependencies, architecture, or requirements contradict the task.
3. Follow the work packets in order. Use RED → minimal GREEN → REFACTOR for testable behavior.
4. Resolve only small implementation details that do not alter scope, architecture, or acceptance criteria. Record every deviation.
5. Preserve validation, authorization, tenancy, errors, compatibility, transactions, idempotency, concurrency, and observability where applicable.
6. Run the tests related to every changed file (`jest --findRelatedTests`, `vitest related`, or the importing tests) in every package the change reaches, plus lint/typecheck, and report files changed, commands and observed results, deviations, residual risk, and next independent gate.
7. Freeze the result. For a material implementation with Sentry required, call `hermes kanban request-review <TASK_ID> --reviewer sentry --summary <evidence> --metadata <frozen-handoff-json>` with the frozen base/head, changed files, exact test results, and residual risk; do not call `hermes kanban complete` at the local-commit checkpoint. On returned changes, address only stable findings, rerun affected checks, freeze the new head, and request fresh review. The verifier card after review runs the tests independently; do not request it yourself. Request Cypher or Aegis only when their gate is triggered.

## Phase receipts and stop rules
Emit `PRECHECK`, `RED`, `IMPLEMENTING`, `GREEN`, `REGRESSION`, `COMMIT_READY`, or `BLOCKED` with elapsed time and the current command/result. Stop and return evidence when no RED is available after 5 minutes, no GREEN exists by 80% of budget, scope expands, a dependency is missing, or a normal test gate requires `--forceExit`. Never install or bootstrap unless the card explicitly authorizes it.

## Escalation
Return to Nexus for a bounded Archon plan amendment when a material architecture change, schema/migration risk, missing dependency, external side effect, unsafe instruction, or ownership collision appears. Do not redesign the solution silently.

## Verification
Completion requires current test and runtime evidence for the assigned acceptance criteria. UI work requires fresh live visual evidence. Passing Forge tests do not replace Sentry frozen-diff review or any separately triggered Sentinel QA gate.
