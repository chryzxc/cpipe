# Route Recipes

Recipes are conditional lifecycle examples, not fixed teams. Select only triggered nodes; `task-graph-flow.md` determines dependencies and waves. Parallel rules and token budgets live in `team-config.yaml` (`parallelism`, `token_policy`).

- **Fast lane (mechanical):** triggers met (≤2 files, no contract change, existing coverage) → Implementer direct → same-card Reviewer review. No Designer/Planner.
- **Tiny:** Lead/Implementer → focused check → Reviewer only for behavior/contracts.
- **Bug:** `Investigator/debugging` → regression contract → domain Implementer → relevant Verifier + Reviewer → recovery → affected gates.
- **Frontend:** parallel reference/repository reads → optional `Investigator/frontend-architecture` → approved contract → `Implementer/frontend` → `Verifier/frontend-visual` + `Reviewer/frontend-code`.
- **Backend/API:** optional architecture → `Implementer/backend` (+ `/database`) → `Verifier/api-integration` + `Reviewer/code` + triggered Security.
- **Cross-stack:** parallel reads → one contract owner → disjoint frontend/backend/data nodes → integration/freeze → targeted Verifiers + Reviewer.
- **Parallel gates:** one frozen SHA → Reviewer + Verifier + Security Reviewer cards dispatched concurrently (no shared output) → Coordinator reconciles (most severe verdict wins; card holds until all return).
- **Batched execution:** ≥2 queued small cards in the same module → one Implementer session, max 3 cards, one frozen commit per card, zero shared files.
- **Spike:** question → Spike Explorer sandbox repro → `SPIKE_VERDICT` → optional Planner plan for the winning option.
- **Performance audit:** Planner + `my-optimization-audit` (baseline, findings PERF-###) → Implementer fixes → Verifier re-measurement delta → verdict.
- **Proactive audit (scheduled):** Auditor scan script → LLM triage of pre-filtered output → PERF-### cards → same performance-audit chain.
- **Refactor:** characterization → `Implementer/refactor` → regression → `Reviewer/code`.
- **Review-only:** frozen target → Reviewer + targeted Verifiers/Security; no edits.
- **Production go-live:** final gates + `my-production-readiness` (Verifier evidence + Release Engineer development-platform sections) → GO / GO_WITH_RISKS / NO_GO on the release card → explicit the operator approval; deployment execution is outside this orchestrator.
- **Delivery:** final gates + explicit `CREATE_PR` → `PR Delivery`; never merge/deploy implicitly.
- **Security audit:** Security Reviewer read-only audit → Coordinator reconciliation → optional blocked Security Tester engagement request.
- **Active validation:** confirmed Security Reviewer hypothesis + authorization receipt → Security Tester → Security Reviewer interpretation → optional Implementer remediation.
- **Security remediation:** Security Reviewer finding/evidence → Implementer bounded fix → same-card Reviewer review → triggered Verifier + Security Reviewer validation.
- **Release readiness:** Verifier application evidence + Release Engineer development-platform evidence → Coordinator readiness report → separate the operator delivery approval.
