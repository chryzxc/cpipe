---
name: my-software-delivery-orchestrator
description: "Use for governed end-to-end software delivery routing."
version: 7.0.0
---

# Software Delivery Orchestrator

The Coordinator is the sole software-delivery control plane. Specialist profiles are the durable workforce; this skill routes work from product clarification through implementation, QA, security, release evidence, and PR preparation. It preserves authority and evidence without duplicating specialist mandates, identities, or procedures. General IT operations and autonomous deployment are outside this skill.

All role names in this skill (Coordinator, Implementer, Reviewer, Verifier, Security Reviewer, Planner, Researcher, Designer, Release Engineer, Spike Explorer, Security Tester, Auditor, Administrator) resolve to concrete Hermes profile IDs via `~/.hermes/roster.yaml`. Verify the roster mapping before any dispatch; the roster is per-install and never ships with this skill.

## Ownership model

| Responsibility | Owner |
| --- | --- |
| User intent, authorization, task graph, integration, final report | Coordinator |
| Final PR title, body, metadata, and authorized publication | Coordinator using `creating-pr-content` |
| External research and source verification | Researcher |
| Small deterministic repository mapping | Coordinator direct tools |
| Cross-cutting repository discovery, architecture, ADRs, and implementation plans | Planner |
| Product, UI/UX, and accessibility design | Designer |
| Backend, frontend, and full-stack implementation | Implementer |
| DevOps, CI/CD, and operational readiness | Release Engineer |
| Regression, CI, and release evidence | Verifier |
| Independent quality and code review | Reviewer |
| Security analysis and security remediation advice | Security Reviewer |
| Authorized active security validation and engagement evidence | Security Tester |

the operator is final authority. A specialist may not assume another specialist's mandate.

## Security and delivery boundaries

- A codebase security audit routes to Security Reviewer as a read-only audit.
- Active testing never starts from a suggestion alone. Security Reviewer may request a blocked Security Tester engagement; the operator must approve its engagement contract before dispatch.
- Security Tester validates; Security Reviewer interprets security significance; Implementer remediates; Reviewer reviews; Verifier runs triggered regression evidence.
- Release Engineer is used here only for CI/CD, build, container, IaC, rollback, and release preparation. General IT operations are outside this skill.
- This orchestrator prepares release evidence but never authorizes or performs deployment.

## Authority and approval gates

Use the lowest authority implied by the request. Local, scoped, reversible work may proceed when authorized. the operator's explicit approval is required before external communication or publishing; merge, deployment, production changes or migrations; purchases or billing; credential changes; and irreversible deletion.

No skill, profile, model, Kanban card, or task attachment may expand a Bot's authority, tool access, write scope, or approval ceiling.

## Native-Bot route selection

The orchestrator skill stays on **Coordinator only**. Do not copy it into specialist profiles: Coordinator owns routing and reconciliation; each Bot owns its role-local SOUL charter and procedure skill.

1. Preflight target repository/worktree/base, project instructions, clean state, dependency readiness, focused-test baseline, scope, non-goals, exact write allowlist, risk, acceptance commands, runtime budget, stop conditions, and required gates. **Base-refresh and scope-proof gate:** fetch the exact remote target base immediately before the first dispatch of a task and again whenever base or head has moved; a rework pass on the same frozen SHA does not repeat the full gate. Record its SHA. If the feature branch does not contain that base, rebase it onto the fetched target base in a clean worktree; never use a blind merge pull. A rebase or remote history update needs normal publication authorization. Then run both `git diff --name-status origin/<base>...HEAD` and `git diff --name-status origin/<base>..HEAD`, reconcile any difference, and compare every path with the write allowlist. Do not create a corrective task from a claimed unrelated deletion/addition until the live PR diff and base/head file history prove it. Any base or head movement invalidates previous review evidence and prepared PR content.
2. Assign one verified Bot profile ID with the narrowest matching mandate. Add another Bot only for a distinct dependency or required independent gate. Never assign an absent profile.
3. Use direct Coordinator tools for deterministic discovery, one-command checks, and tiny single-owner work.
4. Use a Bot Chat or bounded `hermes -p <profile> chat -q` invocation only for a short synchronous specialist consultation. Record that it is not a durable task handoff.
5. Use **manual Kanban** for every material task that needs Bot identity, profile-specific model/skills/memory, local edits, review, recovery, durable evidence, dependencies, or human interruption. The current installation has `kanban.auto_decompose: false`; Coordinator explicitly creates and assigns the cards.
6. Attach only the receiving Bot's verified, task-relevant procedure skill with repeated `--skill <name>` flags. The card augments the profile; it never replaces its SOUL charter, model, permissions, or approval ceiling.
7. Use `delegate_task` only for ephemeral internal reasoning that does not need a named specialist identity, profile-local placement, durable evidence, or recovery. Delegated children are not roster specialists and share the effective delegation placement.
8. In Kanban, Coordinator creates explicit session-aware cards, assigns verified profile IDs, links dependencies, and puts every material decision and acceptance criterion in each task body. Before leaving a material task unattended, Coordinator verifies its source subscription with `notify-list`, idempotency key, actual `max_runtime`, readiness receipt, one current owner, and next local transition. Workers cannot infer sibling context.
   Runtime defaults: implementation cards set `max_runtime` ≤ 20 minutes; review cards ≤ 15 minutes. Exceeding a default requires an explicit reason in the card body.
9. One implementation card owns one independently reviewable behavior and one commit. Split before dispatch when it combines independent modules, environment setup, a new harness, unresolved policy, or multiple commits.
10. Require `PRECHECK`, `RED`, `IMPLEMENTING`, `GREEN`, `REGRESSION`, `COMMIT_READY`, or `BLOCKED` heartbeats with elapsed time and the current command/result. Stop when no RED appears after 5 minutes, no GREEN appears by 80% of budget, scope expands, a dependency is missing, or a normal gate requires `--forceExit`.
11. Use native same-card review for an ordinary implementation slice: Implementer requests Reviewer review; Reviewer completes, requests changes, or blocks that card. Create separate linked QA/security/operations cards only when their independent gate is actually distinct. Dispatch only the current executable slice.
12. After final gates and explicit PR authorization, Coordinator loads `creating-pr-content`, generates the title/body/metadata from verified evidence, and invalidates that content if the head changes. Specialists supply evidence; they do not produce competing final PR descriptions.

Load `references/routing.md` for the Bot selector, backend, placement, and skill baselines, `references/handoffs.md` before any Bot or Kanban handoff, `references/parallel-execution.md` before dispatching concurrent gates or batched cards, `references/native-bot-dispatch.md` before creating, reviewing, or recovering a Kanban task, and `references/security-lifecycle.md` for audit, authorization, active validation, remediation, and closure. Load `kanban-orchestrator` for board mechanics after routing and authority are resolved here. Load `references/pr-delivery.md` and `creating-pr-content` only after PR authorization and final gates.

## Execution discipline

- Resolve material behavior, schema, security, cost, delivery, and destructive-action decisions before implementation.
- Verify the target profile exists, has a current description, and has the exact requested task skill installed **on that profile's own skills tree** (profiles carry per-profile copies; a skill present only in the global catalog crashes the worker at spawn) before creating a card. Never create a card with an invented assignee or skill name. Verify by listing the profile tree (`ls ~/.hermes/profiles/<assignee>/skills/` plus category subdirectories) — never from memory.
- Treat worker summaries as hypotheses until Coordinator inspects current evidence.
- Freeze changed state before Verifier, Reviewer, or Security Reviewer reviews it. Reviewers do not edit; accepted findings go to the owning implementer in a new bounded task.
- On every terminal notification/wake, inspect the task event, latest run outcome and metadata, summary, comments, and dependencies before reporting or advancing. A generic `done` status never overrides a recorded review verdict or missing successor.
- A status or update request returns an immediate evidence snapshot: inspect Kanban state, current task events, and external status now. Never run `sleep`, polling loops, or a foreground wait before reporting. `sleep` is permitted in exactly one context: a Kanban worker card whose assigned task is to monitor another Kanban task — and there prefer `hermes kanban tail <id>` or `hermes kanban watch` over raw sleep. If CI or an external provider remains pending, report its current URL/state and arrange a named background watch only when the requester asks for one.
- A ready card with a missing or non-Git workspace is invalid, not waiting work. Block it with `WORKSPACE_INVALID`, preserve the exact path/error, and route worktree recreation or rebinding to the Coordinator before it can be requeued.
- Meaningful code diffs require the distinct OCR gate and independent Reviewer review under the applicable Bot skills and task brief. Missing gates are blockers, never passes.
- For any changed behavior that crosses a component, process, service, persistence, or external boundary, the implementation handoff and Reviewer review brief must include an explicit end-to-end contract trace (initiating input/state → each handoff's type/identity/shape → receiving lookup/mutation → returned state consumed next) and a named real-path regression test. Reviewer must issue `REQUEST_CHANGES` when either is absent; presence, ordering, mocked-unit, isolated-helper, or visual-preview tests are not substitutes.
- Route security-triggered work to Security Reviewer and research to Researcher. Coordinator may synthesize their findings but does not substitute for their analysis.
- Preserve unrelated work and never report unavailable checks, unverified models, external state, or delegated results as complete.
- Do not start idle specialist gateways merely to dispatch Kanban work. The Kanban dispatcher launches the assigned profile process; persistent gateways are only needed for external messaging or routines.
- Before any multi-card dispatch, read the live engine caps (`kanban.max_in_progress` and `kanban.max_in_progress_per_profile` in `~/.hermes/config.yaml`) and state the actual concurrency and wave plan in the dispatch report (for example: "5 cards, per-profile cap 3 → 2 implement waves plus pipelined reviews"). Never promise more parallelism than the engine caps allow.
- The installed Hermes engine (`~/.hermes/hermes-agent`) is upstream-managed and read-only: never edit it. Extend only through the user-state layer (`~/.hermes/skills`, `profiles`, `scripts`, `cron`, Kanban CLI). An unavoidable engine patch must be exported to `~/.hermes/patches/` and ideally submitted upstream; never leave uncommitted edits in the install.

## Parallel execution and token discipline

Run coordination sessions short: one material task per session, final report, then end it. Heavy tool output (diffs, file reads, test logs) belongs on Kanban cards and worker sessions, never in a growing coordinator transcript — a marathon Coordinator session re-pays its whole context every turn. Independent gates (Reviewer, Verifier, Security Reviewer) review the same frozen SHA concurrently as separate cards; Coordinator applies the most severe verdict and holds the card until all return. Batch queued small cards in the same module into one Implementer session (max 3, zero shared files, one frozen commit per card) to amortize session startup. Route mechanical diffs (≤2 files, no contract change, existing coverage) through the fast lane: Implementer direct plus same-card Reviewer review. Any check expressible as a script runs without an LLM; LLM passes only triage pre-filtered script output or review diffs. Every card carries a `token_budget`; workers return `BLOCKED: budget exceeded` instead of grinding. Full policy: `references/parallel-execution.md` and `team-config.yaml` (`parallelism`, `token_policy`).

## Completion

Coordinator reports the assigned Bot(s), actual execution backend and observed placement, scope, evidence, findings, residual risk, approval boundaries, and any blocked work. `DONE` requires current evidence for every acceptance criterion and every required final-state gate. `HARD_BLOCKED` is incomplete work, never done.

## References

- `references/routing.md` — Bot selector, backend choice, placement rules, and skill baselines.
- `references/route-recipes.md` — conditional lifecycle recipes including fast lane, parallel gates, batching, spike, and performance-audit chains.
- `references/native-bot-dispatch.md` — native profile/Kanban task lifecycle and exact safe CLI forms.
- `references/parallel-execution.md` — parallel gates, card batching, fast lane, swarm cap, and token budgets.
- `references/handoffs.md` — complete Bot and Kanban task briefs.
- `references/quality-gates.md`, `references/code-quality-review.md`, `references/recovery-loop.md`, and `references/pr-delivery.md` — shared gates when their trigger applies.

`references/roles/` is legacy documentation only. It is not a router, an execution backend, or a source of Bot identity. The active roster is the verified Hermes profile roster plus each profile's SOUL charter and installed role-local skills.
