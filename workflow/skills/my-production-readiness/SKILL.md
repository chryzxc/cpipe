---
name: my-production-readiness
description: "Run the production go-live gate before any release."
version: 0.1.0
---

# Production Readiness Skill

The final go-live gate, run by Verifier (evidence owner) with Release Engineer (operational owner) supplying the ops sections. Verdict: `GO`, `GO_WITH_RISKS` (named, accepted by the operator), or `NO_GO` — recorded on the release card with evidence per line. Nothing reaches production without this pass.

## When to Use

- Before a first deploy, a major feature release, a migration touching persisted data, or an infrastructure change.
- Not for routine iterative PRs inside an already-live service with unchanged operational shape.

## Gate checklist (every line gets evidence or a named owner)

**Correctness & quality**
1. Acceptance criteria met with current evidence; known defects listed with severity and workaround.
2. Focused + full regression suites green; untested areas explicitly listed as residual risk.

**Operational (Release Engineer)**
3. Deployment steps scripted/rehearsed (dry-run evidence); rollback path tested, not theoretical.
4. Migrations: backup taken, forward-compatible or paired rollback, data-loss blast radius stated.
5. Monitoring/alerts cover the new failure modes; dashboards updated; on-call runbook entry written.

**Security (Security Reviewer verdict referenced, not duplicated)**
6. Security gate verdict current for the shipped head (stale verdict = re-run; base movement invalidates).
7. No secrets in code/config/images; dependency CVEs at or above the agreed floor triaged.

**Performance & capacity**
8. Load/latency evidence for expected traffic (or `my-optimization-audit` baseline cited); resource ceilings known.
9. Rate limits, backpressure, and graceful degradation behavior stated.

**Continuity**
10. Feature flags/kill switches documented where applicable; customer-facing change notes drafted.
11. `STANDARDS.md` and the repo map updated in this release's cards; ADRs accepted for its decisions.

## Verdict rules

- Any unevidenced line → `NO_GO`, never silence.
- `GO_WITH_RISKS` lists every accepted risk with the operator's named acceptance.
- The verdict is scoped to the exact frozen head; any head movement voids it (same rule as review evidence).

## Output

One evidence-format block per checklist line: `GATE-<n> · pass|risk|fail · evidence | owner` then the verdict line. No prose beyond it.
