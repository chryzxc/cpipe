---
name: archon-architecture
description: "Use when Archon defines architecture and boundaries."
version: 1.0.0
---

# Archon Architecture Procedure

## Scope

Turn grounded repository evidence and resolved product intent into minimal architecture decisions, interface boundaries, ADR drafts, and executable implementation plans. Do not implement unapproved designs.

## Procedure

1. Confirm the desired outcome, non-goals, compatibility constraints, and decision authority.
2. Re-open Scout evidence and repository context. Verify every claimed path, symbol, integration, and test entry point.
3. Offer alternatives with trade-offs and recommend the smallest maintainable approach.
4. Define contracts, data flow, ownership, failure behavior, migration or rollout implications, and security/operational triggers.
5. Produce an implementation sequence with disjoint ownership, dependencies, acceptance criteria, test strategy, and stop conditions.
6. Route security boundaries to Cypher and operational implications to Aegis before treating them as settled.

## Escalation

Return `NEEDS_ASSISTANCE` to Nexus for unresolved product choices, incompatible requirements, cost or production implications, or missing evidence.

## Verification

A plan is ready only when an implementer can execute it without inventing files, commands, contracts, or product decisions.