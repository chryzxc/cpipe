# Maintainability and Refactor Gate

Authoritative source for `MAINT-N` severity, risk, remediation eligibility, and refactor routing. `code-quality-review.md` owns the detailed review rubric and thresholds. Optimize repairability, not line count or fashion.

## Scope

Review the complete changed behavior and minimal surrounding path. Refactor only changed code, minimal adjacent code required for the proven defect, or a deeper shared mechanism when a shallow fix preserves the bug class and remains within clarified scope/authority. Unrelated legacy complexity is baseline debt.

A valid finding demonstrates concrete cost in tracing, ownership, debugging, testing, change amplification, duplicated rules, hidden state/failure, or unjustified indirection. Repository rules and enforced tooling outrank generic principles.

## Contract

```text
ID: MAINT-N
severity: Important | Medium | Low | Suggestion
evidence: path:line plus callers/analogues
problem: exact cohesion/coupling/ownership/traceability defect
cost: why future change/debug/test is slower or riskier
direction: smallest concrete refactor
risk: SAFE | CAREFUL | RISKY
scope: expected files/boundary
verification: behavior-preservation checks
```

Reject “not best practice,” taste, file length without demonstrated cost, every literal needing a constant, every finite value needing an enum, cosmetic line reduction, speculative abstraction, unrelated cleanup, or rewrite preference without a smaller direction.

## Severity and risk

- **Important:** changed code introduces/materially worsens a repairability defect likely to obstruct future work; blocks completion unless fixed or explicitly risk-accepted where permitted.
- **Medium:** bounded defect with demonstrated cost; automatically remediate when in-scope and `SAFE`/`CAREFUL`.
- **Low/Suggestion:** cosmetic, speculative, or weak; no automatic churn and only under explicit requested cleanup scope.
- **SAFE:** mechanically behavior-neutral with current checks.
- **CAREFUL:** intended behavior-neutral but needs characterization/regression evidence.
- **RISKY:** public API/schema, persistence/concurrency/security, migration, broad architecture, or materially broadened scope. Do not auto-apply; return to clarity/planning.

Generic Medium disposition rules never override automatic eligible `MAINT-N` routing. Correctness `REV-N` may use Critical/High; `MAINT-N` does not.

## Refactor loop

1. Make behavior green; characterize under-specified behavior first.
2. Fresh read-only `Reviewer / code` issues `MAINT-N` using `code-quality-review.md`.
3. Lead validates evidence, severity/risk/scope, deduplicates, and rejects taste/baseline debt.
4. Route validated in-scope Important/Medium `SAFE`/`CAREFUL` to separate `Implementer / refactor`; route Low/Suggestion only when explicitly requested; route `RISKY` to clarity/planning.
5. Implement only named IDs and return finding→change→test mapping.
6. Lead inspects collateral diff and reruns focused plus every invalidated QA/Security/Performance gate.
7. Fresh `Reviewer / code` closes findings only against the final state.
8. Failures enter `recovery-loop.md`.

Reviewer and editor remain separate. Completion requires no Important in-scope `MAINT-N`, all eligible/authorized refactors verified, invalidated gates rerun, fresh final review, and no speculative or unauthorized rewrite.
