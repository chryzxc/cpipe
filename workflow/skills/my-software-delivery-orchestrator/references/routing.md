# Routing

Single source for Bot selection, execution backend, model placement, and skill baselines. Recipes live in `route-recipes.md`; the declarative policy mirror is `team-config.yaml`.

## Bot selector

Choose one primary Bot with the narrowest mandate. Add a second Bot only for a real dependency or independent gate; do not route the same responsibility to competing specialists.

| Need | Primary Bot | Typical follow-up |
| --- | --- | --- |
| External facts, comparisons, source verification | Researcher | Coordinator synthesis |
| "Will this work? which option wins?" throwaway spike | Spike Explorer | Planner only if a plan follows |
| Product flow, UI specification, accessibility design | Designer | Implementer and Verifier |
| Architecture, contracts, ADR, approved implementation plan | Planner | Implementer or Release Engineer |
| Performance/optimization audit (read-only) | Planner with `my-optimization-audit` | Implementer fixes, Verifier validation |
| Small deterministic repository map | Coordinator direct tools | Implementer or Planner |
| Unfamiliar cross-cutting repository, dependency map, blast radius | Planner | Implementer or Release Engineer |
| API, domain, data access, integrations, frontend, or full-stack behavior | Implementer | Reviewer; Verifier/Security Reviewer when triggered |
| Read-only codebase security audit, threat boundary, credentials, auth, privacy, or vulnerability assessment | Security Reviewer | blocked Security Tester request only when live proof is necessary |
| Authorized live validation of a specific Security Reviewer finding | Security Tester | Security Reviewer interpretation, then Implementer remediation when confirmed |
| CI/CD, IaC, containers, development tooling, rollback preparation, or release-platform readiness | Release Engineer | Verifier, Security Reviewer when triggered |
| Regression, CI, integration, visual, performance-validation, or release evidence | Verifier | Reviewer and Coordinator |
| Fresh code/maintainability review, OCR gate | Reviewer | owning implementer for named findings |

| Scheduled proactive audit from script output | Auditor | findings route via performance_audit key |
| Final PR title/body/metadata and authorized create/update | Coordinator using `creating-pr-content` | remote read-back and CI-state report |

## Backend choice

- **Coordinator direct tools:** deterministic reads, one-command checks, tiny one-owner work.
- **Bot Chat or profile invocation:** a short specialist question or bounded synchronous analysis (`hermes -p <profile> chat -q`).
- **Kanban:** durable execution, dependencies, formal gates, review/remediation, audit evidence, human interruption, or profile-specific placement.
- **Fast-lane review:** Reviewer review of a fast-lane-eligible card (≤2 files, no contract change, existing coverage) may run as a bounded `hermes -p reviewer chat -q` profile invocation instead of a dispatched run. All other reviews stay dispatched.
- **`delegate_task`:** temporary internal reasoning only — including yes/no lookups inside a gate. It is never a substitute for a named Bot profile. Children share the effective delegation placement; per-child model/provider fields are unsupported.

A requested model, provider, skill, or profile alias is declarative until verified through the live Hermes profile or task configuration. Never place invented model/provider fields in a native `delegate_task` request.

Every routed task records: primary Bot profile ID; task; execution backend (direct | bot_chat | profile_invocation | delegate_task | kanban); requested and actual provider/model (observed | unverified); required skills and readiness; authority ceiling; token_budget. A fallback must preserve scope and authority, be verified before dispatch, and be disclosed to the operator if it materially changes cost, risk, quality, or provider.

## Skill baselines

| Role | Typical baseline |
| --- | --- |
| Planner / technical-design | `plan` |
| Investigator / repository-map | `codebase-inspection` |
| Investigator / debugging | `systematic-debugging` |
| Implementer variants | `test-driven-development` + exact domain skill |
| Verifier / frontend-visual | `dogfood` + exact browser/framework skill |
| Reviewer / plan | `plan`, `independent-code-review` |
| Reviewer / code (Reviewer) | `frozen-diff-review` + `independent-code-review` + `code-quality-review.md`; impact sweep via `ast-grep` + `my-repo-map`; findings REG/BRK/SIMP/REV |
| Implementer / simplify fix (Implementer, from SIMP findings) | `simplify-code` + `test-driven-development` |
| Implementer / refactor | `maintainability-refactor.md` + testing/domain skill |
| Security Reviewer | narrowest matching security skill |
| Authorized active validation (Security Tester) | `web-pentest` + `my-evidence-format` |
| Performance audit (Planner) | `my-optimization-audit` |
| Performance fix (Implementer) | `my-optimization-audit` (findings section only, fix from PERF-### IDs) |
| Performance validation (Verifier) | `my-optimization-audit` (re-measurement delta pass) |
| Architecture decision / ADR (Planner) | `my-adr` |
| Standards compliance (Implementer implements, Reviewer enforces) | `my-engineering-standards` |
| Production go-live gate (Verifier + Release Engineer) | `my-production-readiness` |
| Plan interrogation before material plans (Planner) | `grill-me` |
| Structural code evidence / mechanical rewrites (Reviewer, Implementer) | `ast-grep` (binary: `sg`) |
| Web app browser validation (Verifier) | `webapp-testing` + `dogfood` |
| REST/GraphQL debugging (Implementer) | `rest-graphql-debug` |
| Supply-chain incident forensics (Security Reviewer) | `oss-forensics` |
| Container operations (Release Engineer) | `docker-management` |
| Watchers/monitoring with dedup (Release Engineer, Auditor) | `watchers` |
| All bots, evidence output | `my-evidence-format` |
| PR Delivery | `github-pr-workflow` after activation |

Load only the 1–3 exact baselines the objective needs; verify readiness before dispatch. Substitute a missing/unready skill with an installed equivalent, a repository/live-help/official-documentation procedure — or return `NEEDS_ASSISTANCE` for a real gap — and record the substitution. The optimization-audit chain is checklist work: Luna/Terra only, never Sol.

Active testing is always a Kanban engagement; it is never run through direct Coordinator tools, Bot Chat, a profile one-shot, or `delegate_task`.

## Failure and completion

- Missing tool/credential or stale command/schema → classify through `recovery-loop.md`; never downgrade evidence.
- Overlap/conflict → keep repository rules, the procedure best fitting acceptance, and stricter safety/evidence; ask the Lead only for a material remaining conflict.

Routing is complete when every baseline is satisfied/substituted, additions have distinct purposes, authority is unchanged, actual use and gaps are reported, and the placement record is filled.

## Subagent placement rules

- `delegate_task` children inherit only the parent's placement (model, tools, permissions) — never the role charter, skill selection, durable evidence, or recovery. Use them only for a specialist's internal sub-checks; never for material implementation or gates.
- `kanban swarm` fits fan-out research: N Researcher/Spike Explorer worker cards, one synthesizer, verdicts merged by Coordinator. Use when one question splits into independent parallel investigations.
- Kanban dispatch is the only spawner of role-bearing workers for material work.
