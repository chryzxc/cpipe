---
name: archon-planning
description: "Use when Archon writes an executable implementation spec."
version: 1.0.0
---

# Archon Spec-Driven Planning

## Scope
Create or amend one canonical implementation specification for an authorized, materially complex task. Do not implement, debug, review, or approve the resulting code.

## Delivery plan cards
For a `Plan:` card from `delivery_submit`, the card brief replaces this procedure: no `ROUTE_DOWN`, no plan file, result on the card, PINS included, within the brief's size.

## Procedure
1. Validate the task envelope: objective, scope, non-goals, inputs, budget, approvals, return type, canonical repository, branch policy, and acceptance criteria.
2. Inspect the smallest relevant repository surface and record exact paths and verified facts.
3. If product intent is unresolved, return a bounded Canvas dependency. If the task is mechanically obvious, return `ROUTE_DOWN` instead of spending high-tier planning tokens.
4. Compare only materially viable approaches. Choose one, record concise trade-offs, and eliminate speculative work using YAGNI and DRY.
5. Write tasks in dependency order. Each work packet must identify exact files, one focused objective, prerequisites, write boundary, and completion evidence.
6. For behavior changes, specify RED → minimal GREEN → REFACTOR: exact test path, failing assertion/behavior, command, expected failure, minimal implementation target, passing command, and regression gate.
7. Include integration, migration, compatibility, security, operations, observability, UI/live-evidence, rollback, and documentation work only when the inspected system requires it.
8. Define reviewer routing: Sentinel for independent quality, Cypher for security, Aegis for operations, and Canvas for product/visual acceptance.
9. Self-review for placeholders, contradictions, ambiguous instructions, missing paths, unverifiable completion criteria, and scope creep.
10. Save only the plan/spec artifact and return its path with a compact Forge handoff. Stop.

## Required plan header

```markdown
# [Change] Implementation Plan

**Goal:** ...
**Canonical repository:** ...
**Branch/base:** ...
**Architecture:** ...
**Tech stack:** ...
**Inputs and acceptance criteria:** ...
**Approval state:** ...
```

## Required task structure
Each task contains `Objective`, `Files`, `Dependencies`, numbered execution steps, exact commands, expected outcomes, verification evidence, risks, and the next required gate. A lower-cost implementer must not need conversation history or unstated assumptions.

## Stop conditions
Stop with `blocker` or `approval-request` for missing repository identity, ambiguous product intent, conflicting requirements, unsafe side effects, unknown migration/rollback behavior, unavailable evidence, or exceeded budget. Never fill gaps with plausible output.
