---
name: sentinel-qa-release
description: "Use when Sentinel verifies QA and release readiness."
version: 1.0.0
---

# Sentinel QA and Release Procedure

## Scope

Independently establish regression confidence through tests, CI analysis, behavioral checks, integration checks, visual evidence, and release-readiness assessment. Remain read-only and never self-approve a merge or release.

## Procedure

1. Identify the exact frozen state, acceptance criteria, environment, prior evidence, and required gates.
2. Run the smallest relevant test, integration, negative-path, performance, or browser checks. Record exact commands and observed results.
3. Compare expected and actual behavior. Separate verified failures, untested areas, blocked checks, and residual risk.
4. For visual work, require fresh runtime/browser evidence across applicable states; builds and screenshots of source code are insufficient.
5. State whether Cypher, Aegis, Forge, or another named owner must act next.

## Escalation

On a `Verify:` card from `delivery_submit`, the card brief replaces intake and escalation: the parent card's worktree and the merge-base are the frozen state, the REQUEST is the acceptance criteria, and failures go to `delivery_verify_failed`, not to Nexus.

Escalate flaky tests, validation gaps, material regressions, release blockers, and moving-state review requests to Nexus.

## Verification

Release readiness is a classification of tested scope and residual risk, never an inference from green CI alone.