---
name: my-adr
description: "Record architecture decisions as numbered ADRs in the repo."
version: 0.1.0
---

# ADR Skill

Lightweight Architecture Decision Records: the durable trail of *why* the code is shaped the way it is. Archon authors and amends ADRs during planning; Sentry verifies changed behavior against them; nobody re-litigates a decision that has an accepted ADR without superseding it.

## When to Use

- A plan introduces or changes a cross-cutting choice: data model, external contract, dependency, concurrency model, security boundary, persistence, build/deploy shape.
- A rejected alternative is expensive to rediscover (that rejection is the ADR's most valuable content).
- Don't use for local implementation details (function choice, file layout) or anything `my-repo-map` already captures.

## Procedure

1. Check `docs/adr/` for an existing ADR covering the decision. Amend or supersede; never write a silent sibling.
2. Next number: zero-padded (`0007-...`). Title states the decision, not the topic.
3. Write the ADR before implementation merges (Archon includes it in the plan's work packets).
4. Status lifecycle: `proposed` → `accepted` (Christian or delegated owner) → `superseded by NNNN`. Never `accepted` without a named decision maker.

## Format (hard cap: 80 lines)

```markdown
# NNNN. <decision statement>
Status: proposed | accepted (by <who>, <date>) | superseded by NNNN
Date: <date> · Scope: <repos/modules touched>

## Context   — the forces at play; what makes this a decision at all
## Decision  — one paragraph, active voice, unambiguous
## Alternatives — each: what it was, why rejected, cost of rediscovery
## Consequences — positives, negatives, and the follow-ups this creates
## Compliance — how a reviewer verifies the code matches this decision
```

## Rules

- One decision per ADR; split coupled decisions into numbered parts.
- `Compliance` must name a checkable signal (file path, test, command) — Sentry cites it when reviewing behavior in that area.
- Superseding keeps the old file, status-flipped, with a pointer both directions.
- The repo map (`my-repo-map`) links ADR numbers from its Contracts and Invariants sections.

## Non-goals

Design essays, meeting notes, task history, or documentation of what the code already self-evidently does.
