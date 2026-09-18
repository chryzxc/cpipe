# Dependency-Aware Parallel Flow

Load whenever work is decomposed across more than one agent or gate.

```text
clarify → preflight → decompose DAG → assign roles/capabilities
→ run ready nodes in parallel waves → integrate → freeze
→ QA/review/triggered specialists in parallel → remediate
→ rerun invalidated gates → final integration → authorized delivery
```

The task graph defines parallelism. Base roles define authority. Adapters narrow responsibility. Skills define technique. Evidence defines completion.

## Task-node contract

```yaml
id: stable-id
objective: one measurable outcome
base_role: engineering-lead | planner | investigator | implementer | verifier | reviewer | security-reviewer | security-tester | pr-delivery
adapter: exact adapter name | none
mode: READ_ONLY | LOCAL_EDIT | LOCAL_COMMIT | REMOTE_BRANCH | ENGAGEMENT_SCOPED_EXTERNAL_TEST | CREATE_PR | MERGE | DEPLOY
depends_on: []
reads: []
writes: []
worktree: canonical | isolated-name
model_tier: FAST | BALANCED | STRONG | CRITICAL
backend: delegate_task | kanban | separate-process | lead
baseline_skills: []
discovery_targets: []
approved_plan: {path: exact-.hermes/plans/file.md, sha256: exact-approved-hash, task_id: plan-task-id}
review_policy: per_task | per_wave | final_only
acceptance: []
verification: []
handoff_to: []
```

## Readiness

A node is ready only when every dependency has current evidence; contracts and user decisions are frozen; backend/capabilities are available or truthfully substituted; ownership is explicit; no same-wave collision exists; and acceptance/verification are executable. Planned implementation also requires an approved plan path/SHA-256/task ID whose evidence, preconditions, writes, RED/GREEN steps, commands, stop conditions, and acceptance IDs match the node. Recompute after every wave or recovery event.

## Parallel safety

```text
parallel_safe =
  dependencies_current
  AND disjoint_write_ownership
  AND shared_contracts_frozen
  AND no_unfinished_input_consumption
  AND no_generated_artifact_or_migration_conflict
```

Safe: independent read-only investigation; implementation with disjoint writes, frozen contracts, isolated worktrees, and one integration owner; QA/Review/Security/Performance on the same frozen state; independent non-mutating tests.

Serialize: product clarification; shared contracts before consumers; overlapping edits/generated artifacts; migrations before dependent code unless compatibility is proven; each implementation packet before its required review; remediation before re-review; dependent packets before an upstream task's required review passes; final verification before delivery.

cypher-finding → human-authorization → wraith-validation → cypher-interpretation → forge-remediation

Wraith cannot run in the Sentry/Sentinel/Cypher parallel review wave and cannot consume an implementation card as implicit authorization.

Different role names do not imply independence. Add a dependency when one consumes another's output or both can mutate the same file, contract, database, service, fixture, generated artifact, or environment.
## Waves and cap

Build maximal safe waves without exceeding the smaller of `execution.max_parallel_workers` and the live backend limit. Native `delegate_task(tasks=[...])` runs one parallel batch; each task gets a complete brief. Never add unsupported per-task model/provider fields.

## Ownership and integration

Parallel editors require isolated worktrees. One owner controls each overlapping behavior/file. Freeze shared contracts before consumers implement. The lead names integration order and verifies handoffs. Ownership collisions return `NEEDS_ASSISTANCE` before editing.

## Frozen-state gates

Record repository, branch/revision, dirty state, diff base, changed/untracked/deleted files, and generated artifacts. Any later source, test, migration, dependency, build, schema, or generated-contract edit invalidates every affected gate.

## Recovery

A failed node pauses descendants, not independent ready work. Register the blocker, continue safe nodes, route the narrowest recovery worker, verify the new state, and resume from the checkpoint.
