# Route Recipes

Recipes are conditional lifecycle examples, not fixed teams. Select only triggered nodes; `task-graph-flow.md` determines dependencies and waves. Parallel rules and token budgets live in `team-config.yaml` (`parallelism`, `token_policy`).

- **Fast lane (mechanical):** triggers met (≤2 files, no contract change, existing coverage) → Forge direct → same-card Sentry review. No Canvas/Archon.
- **Tiny:** Lead/Implementer → focused check → Reviewer only for behavior/contracts.
- **Bug:** `Investigator/debugging` → regression contract → domain Implementer → relevant Verifier + Reviewer → recovery → affected gates.
- **Frontend:** parallel reference/repository reads → optional `Investigator/frontend-architecture` → approved contract → `Implementer/frontend` → `Verifier/frontend-visual` + `Reviewer/frontend-code`.
- **Backend/API:** optional architecture → `Implementer/backend` (+ `/database`) → `Verifier/api-integration` + `Reviewer/code` + triggered Security.
- **Cross-stack:** parallel reads → one contract owner → disjoint frontend/backend/data nodes → integration/freeze → targeted Verifiers + Reviewer.
- **Parallel gates:** one frozen SHA → Sentry + Sentinel + Cypher cards dispatched concurrently (no shared output) → Nexus reconciles (most severe verdict wins; card holds until all return).
- **Batched execution:** ≥2 queued small cards in the same module → one Forge session, max 3 cards, one frozen commit per card, zero shared files.
- **Spike:** question → Probe sandbox repro → `SPIKE_VERDICT` → optional Archon plan for the winning option.
- **Performance audit:** Archon + `my-optimization-audit` (baseline, findings PERF-###) → Forge fixes → Sentinel re-measurement delta → verdict.
- **Proactive audit (scheduled):** Beacon scan script → LLM triage of pre-filtered output → PERF-### cards → same performance-audit chain.
- **Refactor:** characterization → `Implementer/refactor` → regression → `Reviewer/code`.
- **Review-only:** frozen target → Reviewer + targeted Verifiers/Security; no edits.
- **Production go-live:** final gates + `my-production-readiness` (Sentinel evidence + Aegis development-platform sections) → GO / GO_WITH_RISKS / NO_GO on the release card → explicit Christian approval; deployment execution is outside this orchestrator.
- **Delivery:** final gates + explicit `CREATE_PR` → `PR Delivery`; never merge/deploy implicitly.
- **Security audit:** Cypher read-only audit → Nexus reconciliation → optional blocked Wraith engagement request.
- **Active validation:** confirmed Cypher hypothesis + authorization receipt → Wraith → Cypher interpretation → optional Forge remediation.
- **Security remediation:** Cypher finding/evidence → Forge bounded fix → same-card Sentry review → triggered Sentinel + Cypher validation.
- **Release readiness:** Sentinel application evidence + Aegis development-platform evidence → Nexus readiness report → separate Christian delivery approval.
