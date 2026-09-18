# Quality Gates

This file owns lifecycle gates, acceptance evidence, test depth, and invalidation. `code-quality-review.md` owns review dimensions/`REV-N`; `maintainability-refactor.md` owns `MAINT-N`; `recovery-loop.md` owns non-pass routing; `pr-delivery.md` owns publication.

```text
G-1 clarity → G0 preflight → G1 investigation/plan
→ G2 implementation → G3 frozen-state challenge
→ G3A conditional active validation → G4 remediation ↺ affected gates
→ G5 integration → G6 authorized delivery
```

Every gate names target state, owner, evidence, pass condition, and failure action. Never approve a moving target.

## Acceptance ledger

| ID | Criterion/non-goal | Explicit/inferred | Owner | Verification | Latest evidence/result |
| --- | --- | --- | --- | --- | --- |

Every explicit requirement gets an ID. Requirement/state changes invalidate affected work and verdicts. No `PASS` without evidence; unavailable checks route through recovery and remain gaps.

## Criteria

### G-1 — Requirement clarity

Pass when the Lead can state outcome, non-goals, acceptance, material assumptions, and authorization without guessing. Resolve materially different user-owned behavior, data/schema, security, contract, destructive/migration, scope/cost, or delivery choices before Planner/implementation. One obvious low-risk reversible repository-backed assumption may proceed if recorded; otherwise `HARD_BLOCKED` pending the smallest user question.

### G0 — Preflight

Pass when project rules, target/base/dirty state, affected surfaces, risk, ownership, permission, and verification path are known.

### G1 — Investigation/plan

- Bug: symptom reproduced/bounded, current-state root cause evidenced, regression loop defined.
- Feature: analogues/contracts/states/errors/compatibility/migration/rollback understood; 2–3 approaches and recorded design approval when non-trivial.
- Multi-agent or lower-capability execution: fresh `Reviewer / plan` approval for the exact plan path/SHA-256; accepted `PLAN-N` closed; every repository claim re-proved; graph-ready self-contained packages satisfy `task-graph-flow.md` without requiring implementer inference.

Do not implement from unresolved intent, untested guesses, unapproved material design, or an unreviewed dependency graph.

### G2 — Implementation

Pass when focused behavior works, targeted checks pass, scope/ownership are bounded, every changed file is accounted for, and handoff records contracts/assumptions. Planned work must map changes/evidence to the approved plan task and acceptance IDs, record deviations or contradictions, and complete the configured per-task/per-wave Code Review before dependent nodes proceed.

### G3 — Frozen-state challenge

Freeze exact repository/state/diff first.

- **QA:** each relevant criterion has expected/actual evidence and risk-proportionate negative/edge/regression coverage.
- **Code Review:** load `code-quality-review.md` and `maintainability-refactor.md`; compare the exact frozen diff to the approved task packet/acceptance IDs, then correctness/contracts, repository standards, and repairability. No blocking `REV-N`, Important in-scope `MAINT-N`, unresolved required referral, unsupported shortcut, unjustified new/substantially expanded handwritten file beyond configured threshold, or unexplained project-gate gap remains.
- **Security:** trigger on changed trust boundaries/plausible vulnerabilities; no Critical/High blocker remains and residual risk is explicit.
- **Performance:** trigger on explicit/plausible impact; environment, baseline, repeated method, variance, threshold, and result are recorded.

### G3A — Conditional active validation

Run only when Security Reviewer has a stable finding and static evidence cannot answer the named hypothesis. Pass only when the full Security Tester engagement receipt was authorized before dispatch, the exact target/window is covered, the result is one of `CONFIRMED`, `NOT_REPRODUCIBLE`, `PARTIAL`, or `BLOCKED`, and the redacted evidence is retained for Security Reviewer. A missing receipt, target mismatch, expired window, or unsafe stop is `BLOCKED`, never a partial pass. Security Tester never edits code or owns severity.
### G4 — Remediation

Use `maintainability-refactor.md` for `MAINT-N` eligibility and `recovery-loop.md` for routing. Pass only when every accepted finding has current disposition/evidence, collateral scope is clean, and all invalidated checks/reviews rerun on the new frozen state. A new head invalidates affected Reviewer, Verifier, and Security Reviewer evidence.

### G5 — Integration

Pass when the final acceptance ledger, complete diff/manifest, broad relevant checks, contracts/artifacts/migrations, finding dispositions, and independent approvals all cover the same canonical state. Separate introduced, baseline, environment, provider, and inconclusive failures. Security closure must reference the original finding ID and current evidence; green tests alone do not close a security finding.

### G6 — Delivery

Dormant until explicit `CREATE_PR` and G5 pass. Then `pr-delivery.md` governs authorized publication/read-back. Merge/deploy require separate authority.

## Test depth

Broaden by blast radius and repository convention:

1. **Focused:** syntax/compile, direct regression/unit, targeted format/lint/type.
2. **Component/integration:** module/service/component, API/contract/browser, migration forward/rollback, package/build.
3. **Broad regression:** relevant full suite, full lint/type/build when required/affordable, critical E2E, platform matrix.
4. **Operational/external:** CI/provider, deployed health, artifact install, rehearsal, device/browser—only with authorization/access.

A narrower substitute does not make a required broader failing gate green.

## Baseline and invalidation

When feasible compare relevant checks before/after or against a protected clean base. Classify failures as introduced, pre-existing, environment-dependent, provider-dependent, or inconclusive. Never destructively stash/reset/clean user work to create a baseline without authorization.

Later edits invalidate every concern they can affect. Shared utilities, auth, schema, dependencies, build config, public contracts, or uncertain blast radius require conservative re-review. Record the exact state each verdict covers.

## Risk-triggered evidence

- **Frontend:** loading/empty/success/error/permission; keyboard/focus/a11y; responsive/reduced-motion; state races; console/network; backend contracts.
- **Backend/API:** validation/errors; auth/tenancy; compatibility; idempotency/retry/concurrency; downstream failure; serialization/logging.
- **Data:** forward/rollback; existing/malformed data; constraints/indexes; lock/downtime; compatibility window; partial failure/recovery.
- **Platform:** reproducible build; cache/matrix; secrets/config; privilege; rollback/health; artifact integrity.
- **Docs:** trigger when changed public API/config/setup/CLI/migration/operations/user workflow makes current docs inaccurate.

Final evidence lists only gates/roles that actually ran; skips and unavailable checks are explicit.
