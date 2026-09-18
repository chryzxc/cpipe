# Parallel Execution and Token Discipline

Operational rules for cutting wall-clock time without raising token spend. Declarative mirror: `team-config.yaml` (`parallelism`, `token_policy`). Recipes: `route-recipes.md`.

## Parallel independent gates

1. Preconditions: one frozen SHA, all three gates actually triggered by the task brief, each gate has its own Kanban review card with the same frozen handoff metadata.
2. Dispatch Sentry (diff review), Sentinel (QA evidence), and Cypher (security) concurrently. No gate receives another gate's output or intermediate state before returning its verdict.
3. Reconciliation: Nexus applies the most severe verdict (BLOCKED > REQUEST_CHANGES/NEEDS_ASSISTANCE > READY_WITH_RISK > APPROVED/READY). The card holds until every gate returns; a late-arriving more-severe verdict reopens the card.
4. Any base or head movement invalidates all pending gate evidence (existing base-refresh rule).

## Card batching by module affinity

1. Eligible: queued implementation cards touching the same directory/module, each independently reviewable, zero shared files between cards.
2. Limits: max 3 cards per Forge session; one frozen commit per card preserved; batch only when session-boot context (SOUL + repo instructions + module context) is shared.
3. Never batch across authority boundaries, write-allowlist conflicts, or dependency edges.
4. Record the batch in each card body (`batch: <card-ids>`) so reviewers see the shared session.

## Fast lane

1. Triggers (all required): file count ≤ 2; no schema, API, persistence, or UI-contract change; existing test coverage touches the changed behavior.
2. Route: Forge direct plus same-card Sentry review. Canvas and Archon are skipped.
3. Anything failing a trigger routes through the normal selector in `routing.md`.

## Active validation lane

Active Wraith validation is never an independent frozen-diff gate. It begins only after a Cypher finding and engagement authorization, and its result returns to Cypher before any remediation card becomes ready.

## Kanban swarm cap

- Max 2 concurrent Forge workers, only on cards with zero shared files. More workers multiply session boots, not throughput. Do not start idle specialist gateways to swarm.

## Ephemeral sub-checks

- Yes/no or single-fact lookups inside a gate or plan run as `delegate_task` (shared placement) or Nexus direct tools — never a full specialist session or new Kanban card.

## Token budgets

1. Every card body carries `token_budget` (estimate at creation; actuals appended on completion from observed usage).
2. A worker approaching the budget finishes the current step, freezes state, and returns `BLOCKED: budget exceeded` with progress evidence — never grinds past it.
3. Nexus reviews any card exceeding budget before re-authorizing; repeated overruns on the same shape of work feed the weekly metrics digest.

## Session hygiene

1. One material task per Nexus session: preflight, route, reconcile the result, report, end. Start the next task in a fresh session instead of continuing a long transcript.
2. Never pull bulky worker output into the coordinator transcript — link the card ID and read only the verdict/summary lines. The card's own evidence carries the detail.
3. A coordination session that has crossed half the context window must not start new material work: report, end, and continue fresh.
4. The weekly digest flags any single session exceeding 100M tokens — treat a flag as a routing failure to correct, not a cost of doing business.

## Weekly guardrail

- Alert when 60% of the weekly token budget is consumed before midweek. On alert, Nexus runs a routing review (model tiers actually used, non-fast-lane dispatches, batching rate) before further dispatch and reports findings to Christian.

## Deterministic-first

- Any check expressible as a script or CLI (grep, du, EXPLAIN, CVE scan, bundle stats, test run) executes without an LLM. LLM passes triage pre-filtered script output or review diffs only. This applies to gates, audits, and the Beacon nightly pipeline.
