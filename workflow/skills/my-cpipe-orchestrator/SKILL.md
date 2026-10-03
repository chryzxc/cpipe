---
name: my-cpipe-orchestrator
description: "Only for cases delivery_submit does not cover: security engagements, release prep, role exceptions. Never load it to submit, size, fix, or watch a delivery; the delivery_* tool descriptions are the whole procedure."
version: 7.0.0
---

# cpipe Orchestrator

The Coordinator is the sole cpipe control plane. Specialist profiles are the durable workforce; this skill routes work from product clarification through implementation, QA, security, release evidence, and PR preparation. It preserves authority and evidence without duplicating specialist mandates, identities, or procedures. General IT operations and autonomous deployment are outside this skill.

All role names in this skill (Coordinator, Implementer, Reviewer, Verifier, Security Reviewer, Planner, Researcher, Designer, Release Engineer, Spike Explorer, Security Tester, Auditor, Administrator) resolve to concrete Hermes profile IDs via `~/.hermes/roster.yaml`. Verify the roster mapping before any dispatch; the roster is per-install and never ships with this skill.

## Ownership model

| Responsibility | Owner |
| --- | --- |
| User intent, authorization, task graph, integration, final report | Coordinator |
| PR title, body, and metadata (kept current for the whole branch after every push) | Implementer using `creating-pr-content`; Coordinator one final pass when the delivery ends |
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

1. Preflight is the plugin's for `delivery_submit` work: workspace prep resets a fresh worktree onto `origin/<base_branch>` and links or allows installing dependencies, and the implementer runs its own baseline. Do not repeat a base-refresh or allowlist gate before dispatch. For a manually created card, name the repository, base, scope, and acceptance commands in its body. Any base or head movement invalidates previous review evidence.
2. Assign one verified Bot profile ID with the narrowest matching mandate. Add another Bot only for a distinct dependency or required independent gate. Never assign an absent profile.
3. Use direct Coordinator tools for deterministic discovery, one-command checks, and tiny single-owner work.
4. Use a Bot Chat or bounded `hermes -p <profile> chat -q` invocation only for a short synchronous specialist consultation. Record that it is not a durable task handoff.
5. Use **manual Kanban** for every material task that needs Bot identity, profile-specific model/skills/memory, local edits, review, recovery, durable evidence, dependencies, or human interruption. The current installation has `kanban.auto_decompose: false`; Coordinator explicitly creates and assigns the cards.
6. Attach only the receiving Bot's verified, task-relevant procedure skill with repeated `--skill <name>` flags. The card augments the profile; it never replaces its SOUL charter, model, permissions, or approval ceiling.
7. Use `delegate_task` only for ephemeral internal reasoning that does not need a named specialist identity, profile-local placement, durable evidence, or recovery. Delegated children are not roster specialists and share the effective delegation placement.
8. In Kanban, Coordinator creates explicit session-aware cards, assigns verified profile IDs, links dependencies, and puts every material decision and acceptance criterion in each task body. Before leaving a material task unattended, Coordinator verifies its source subscription with `notify-list`, idempotency key, actual `max_runtime`, readiness receipt, one current owner, and next local transition. Workers cannot infer sibling context.
   Runtime defaults: implementation cards set `max_runtime` 60 minutes (one feature is one session; a timeout restarts it cold); map/plan cards 20 minutes; review cards ≤ 15 minutes.
9. All code work goes through `delivery_submit`: never phase cards or hand-written chains. `size=content` (text, copy, markup, styles, docs, a config value): one implementer card and a same-card wording review. `size=small` (the default: any feature or fix one developer does in one sitting, even across several files): a frontier planner card writes a short plan in the PLAN FORMAT, then one implementer card builds, tests, pushes, and opens a draft PR ending in `## How to test`, the same-card review follows (at most 3 rounds), then a verify card runs the tests and marks the PR ready. `size=large` only for work spanning separately owned modules or needing a design decision: map, then plan, then the same build, review, and verify. Pass `verify: false` only when the operator asks to skip verification. When the operator reports a problem found while testing (`fix <PR or card> <issue>`), call `delivery_submit` with `fix_of=<PR URL or card id>` and `request=<the issue>`: one fix card on the same branch updates the PR, then it is verified. No Coordinator hop between stages. The `request` is a brief (goal, acceptance criteria, named files), never the conversation: workers see only their card.
10. CLARIFY BEFORE SUBMITTING (small and large). Write the brief's acceptance criteria first. If any criterion needs a guess about product behavior (which page or screen, which users or roles, what happens on error or empty input, what must stay unchanged, wording the user must approve), ask ONE `clarify` before `delivery_submit`: up to 3 questions, 2-4 concrete choices each, recommended first. Never ask what the code can answer (file names, existing patterns): the planner reads the code and raises OPEN DECISIONS itself, and a blocked plan card comes back to you as a card decision (below). If `clarify` has no user, ask the same questions once in text and submit only after the answer.
Commit count is never a review finding: history is squashed at merge, so never request a squash or a force-push.
11. Require `PRECHECK`, `RED`, `IMPLEMENTING`, `GREEN`, `REGRESSION`, `COMMIT_READY`, or `BLOCKED` heartbeats with elapsed time and the current command/result. Workers finish rather than stop: they install missing dependencies, change any file the REQUEST needs (listed as `OUT_OF_PLAN`), and near the end of the budget push what is green with `READY_WITH_RISK`. A block is only a product, security, or destructive/external decision.
12. Use native same-card review for an ordinary implementation slice: Implementer requests Reviewer review; Reviewer completes, requests changes, or blocks that card. Create separate linked QA/security/operations cards only when their independent gate is actually distinct. Dispatch only the current executable slice.
13. The implementer writes the PR title/body/metadata with `creating-pr-content` and rewrites them after every push to describe the whole branch. When the delivery ends, Coordinator does one final pass adding only what the user's conversation knows (why, decisions, linked issues) and keeps the evidence; other specialists supply evidence only. No user, bot, or tool names, card ids, or local paths in the PR.

Load `references/routing.md` for the Bot selector, backend, placement, and skill baselines, `references/handoffs.md` before any Bot or Kanban handoff, `references/parallel-execution.md` before dispatching concurrent gates or batched cards, `references/native-bot-dispatch.md` before creating, reviewing, or recovering a Kanban task, and `references/security-lifecycle.md` for audit, authorization, active validation, remediation, and closure. Load `kanban-orchestrator` for board mechanics after routing and authority are resolved here. Load `references/pr-delivery.md` only after PR authorization and final gates.

## Execution discipline

- Resolve material behavior, schema, security, cost, delivery, and destructive-action decisions before implementation.
- Verify the target profile exists, has a current description, and has the exact requested task skill installed **on that profile's own skills tree** (profiles carry per-profile copies; a skill present only in the global catalog crashes the worker at spawn) before creating a card. Never create a card with an invented assignee or skill name. Verify by listing the profile tree (`ls ~/.hermes/profiles/<assignee>/skills/` plus category subdirectories) — never from memory.
- Treat worker summaries as hypotheses until Coordinator inspects current evidence.
- Freeze changed state before Verifier, Reviewer, or Security Reviewer reviews it. Reviewers do not edit; accepted findings go to the owning implementer in a new bounded task.
- A ✔ done notification needs no inspection: the engine promotes linked children, so relay the result and stop (`delivery_status` only if unclear). A ⏸ blocked, gave_up, or crashed wake is decided from the block reason and `delivery_status`. A generic `done` status never overrides a recorded review verdict or missing successor. Progress reaches the operator through push — terminal wakes via subscriptions, the delivery monitor's escalations and notices, and its stall digest — never through the operator asking, and never through foreground watching (`sleep`, polling, process-manager checks).
- Record a decision as ONE card comment of at most 200 characters starting with the literal tag `CONTINUATION:` and one decision — `same-card correction`, `promote gate <card-id>`, `successor <card-id>`, `blocker <name> (owner <profile>)`, or `final report <PR url>` — plus a short why. Required after every block decision and on a done card with no linked child (a linked child is the continuation). No evidence dumps, receipts, run ids, or SHAs: comments are copied into later workers' briefs. Replayed notifications are no-ops once the marker exists. Before any manual promote, verify canonical status, workspace validity, and that no newer card supersedes the same scope (record supersession as a comment linking the replacement card-id).
- A status or update request returns an immediate evidence snapshot: call `delivery_status` first and answer from its verdicts (PROGRESSING / WAITING(<named>) / STUCK(<cause>) with its `next`), never from memory or a worker's last message. Never run `sleep`, polling loops, or a foreground wait before reporting. `sleep` is permitted in exactly one context: a Kanban worker card whose assigned task is to monitor another Kanban task — and there prefer `hermes kanban tail <id>` or `hermes kanban watch` over raw sleep. If CI or a PR remains pending, report its current URL/state. Never say a card "will resume" after a merge or CI run unless `delivery_watch` registered it.
- The gateway dispatcher alone decides spawning and concurrency (engine caps plus its memory-pressure guard). Workers are disposable *instances*: one bot may legitimately run several workers at once, and a busy bot is never a blocker. Never block, hold, or "park" a `ready` card for capacity reasons. Never run `hermes kanban dispatch` yourself: it bypasses ESTOP and the gateway's caps. A `ready` card that does not start is the delivery monitor's to name (`delivery_status`); act on its `next`.
- The workflow advances without the operator asking. Pre-create every already-known successor in a chain as a linked child (`--parent <id>`) at planning time so the engine promotes it the moment its parent is done, with no coordinator turn in the critical path; a linked child counts as the parent's continuation. Hand implementation off for review with `kanban_request_review` so the card enters the native review lane the dispatcher claims automatically — never as a separate `ready` "Review …" card.
- When the global emergency stop (`~/.hermes/ESTOP`) is engaged, the dispatcher and every cron job — including the stall supervisor — are paused on purpose. Tell the operator how many ready/review cards are waiting and ask whether to `hermes resume`; never lift it yourself and never bypass it with `hermes kanban dispatch`, which ignores ESTOP.
- Repeated notices (same card, cause and signature) get no reply; a new one gets one line: card, verdict, next step.
- A repo convention the operator states (base branch, branch naming, draft-PR policy, forbidden tools) is saved to `~/.hermes/delivery/projects.yaml` under `projects.<project>` (`base_branch`, `branch_prefix`, `env`, `conventions`) before replying; `delivery_submit` stamps it into every card and workspace prep flags `CONVENTION_MISMATCH`.
- Open a draft PR at the first pushable commit; review and verify gate only "ready for review" and merge.
- Never unblock a card whose failure signature is unchanged (`IDENTICAL_FAILURE`) without new input. `APPROVAL_NEEDED` (a worker hit an approval prompt), `JUDGE_ERROR` (goal-mode judge failed) and `WIP_PRESERVED` (a rejected terminal call kept the worktree) are named blocks: read the card, decide once, record `CONTINUATION:`.
- Past ~150 turns or 3 compactions, offer the operator a handoff to a fresh session seeded with `delivery_status` for the chain.
- Declare at planning time any gate a worker cannot perform (native UI interaction, a live browser session, physical devices, operator credentials) and route it to an explicit operator-verification card, instead of letting a worker discover it and block mid-chain. A "prepare dependencies" card resolves lockfile drift (`npm install`/lockfile update committed on the branch) before `npm ci`-style steps run, rather than blocking on it.
- Every `block` records a concrete reason and the owner who can clear it; a reasonless block is invisible to recovery and the supervisor flags it (`BLOCKED_NO_REASON`).
- Independent slices run in parallel by default. When a request or plan yields two or more slices with distinct modules and zero shared files, create them as one wave immediately — never wait for the operator to request parallelism. A serial chain requires an explicitly stated dependency reason on each card. The operator mentioning parallelism is a reminder, not a trigger.
- A ready card with a missing or non-Git workspace is invalid, not waiting work. Block it with `WORKSPACE_INVALID`, preserve the exact path/error, and route worktree recreation or rebinding to the Coordinator before it can be requeued.
- A card whose assigned profile cannot authenticate or start is a readiness blocker, not retry fuel. On the first credentials/startup failure (`READINESS_BLOCKER`), authenticate the named profile/provider before any re-dispatch and keep the card blocked until then. A terminal run with no usable response (`WORKER_EMPTY_RESULT`, e.g. a goal-mode worker out of turns) is a distinct worker outcome routed to exactly one bounded recovery decision. Past the engine failure limit (`RESPAWN_LOOP`), stop respawning. Every recovery decision preserves the exact workspace (path@sha), current commit/status, and dependency graph, and keeps the card's `notify+wake` subscription so the decision reaches the operator.
- Meaningful code diffs require the distinct OCR gate and independent Reviewer review under the applicable Bot skills and task brief. Missing gates are never passes. On HIGH tier they are blockers. On LOW/MED tier, a gate that cannot run because the environment lacks a tool, config, dependency, or secret (OCR, linter config, `node_modules`, env vars) is recorded as `READY_WITH_RISK` with the exact gap in the PR evidence block, and delivery continues. Blocking for a human is reserved for decisions only a human can make. Review cycle contract (`references/code-quality-review.md`): the first `REQUEST_CHANGES` enumerates every finding in one pass (`REV-001…N`); rework reviews are delta-scoped to `last_reviewed_head..new_head` plus prior-ID verification; after 3 cycles on one card, reconcile scope (split / park the unsatisfiable finding / accept risk) instead of iterating.
- For any changed logic (not static text, markup, links, or styles) that crosses a component, process, service, persistence, or external boundary, the implementation handoff includes an end-to-end contract trace (initiating input/state → each handoff's type/identity/shape → receiving lookup/mutation → returned state consumed next) and a test that calls the real changed code; mocking a dependency at a boundary is fine. A deeper harness (real router, database, browser) is required only when the request or plan names it; otherwise a missing one is residual risk, not `REQUEST_CHANGES`.
- Route security-triggered work to Security Reviewer and research to Researcher. Coordinator may synthesize their findings but does not substitute for their analysis.
- Preserve unrelated work and never report unavailable checks, unverified models, external state, or delegated results as complete.
- Do not start idle specialist gateways merely to dispatch Kanban work. The Kanban dispatcher launches the assigned profile process; persistent gateways are only needed for external messaging or routines.
- Before any multi-card dispatch, read the live engine caps (`kanban.max_in_progress` and `kanban.max_in_progress_per_profile` in `~/.hermes/config.yaml`) and state the actual concurrency and wave plan in the dispatch report (for example: "5 cards, per-profile cap 3 → 2 implement waves plus pipelined reviews"). Never promise more parallelism than the engine caps allow. When creating more than one card in a wave, the creation report itself states that wave plan and marks which cards are LOW fast-lane, so the operator sees expected completion order up front.
- The installed Hermes engine (`~/.hermes/hermes-agent`) is upstream-managed and read-only: never edit it. Extend only through the user-state layer (`~/.hermes/skills`, `profiles`, `scripts`, `cron`, Kanban CLI). An unavoidable engine patch must be exported to `~/.hermes/patches/` and ideally submitted upstream; never leave uncommitted edits in the install.

## Parallel execution and token discipline

Run coordination sessions short: one material task per session, final report, then end it. Heavy tool output (diffs, file reads, test logs) belongs on Kanban cards and worker sessions, never in a growing coordinator transcript — a marathon Coordinator session re-pays its whole context every turn. Independent gates (Reviewer, Verifier, Security Reviewer) review the same frozen SHA concurrently as separate cards; Coordinator applies the most severe verdict and holds the card until all return. Batch queued small cards in the same module into one Implementer session (max 3, zero shared files, one frozen commit per card) to amortize session startup. Route mechanical diffs (≤2 files, no contract change, existing coverage) through the fast lane: Implementer direct plus same-card Reviewer review. Any check expressible as a script runs without an LLM; LLM passes only triage pre-filtered script output or review diffs. Every card carries a `token_budget`; workers return `BLOCKED: budget exceeded` instead of grinding. Full policy: `references/parallel-execution.md` and `team-config.yaml` (`parallelism`, `token_policy`).

## Asking for a decision

When a card needs the operator (routed to triage, blocked `needs_input`, `APPROVAL_NEEDED`, a scope or risk choice), ask with the `clarify` tool, never as prose: one question naming the card and what stopped it, 2–4 choices, recommended first, each choice a concrete action (e.g. `Allow: widen scope to <files> + tests`, `Reject: accept as residual risk, close route-scoped`). Apply the answer as a `continue <id> <answer>` below. If `clarify` is unavailable, times out, or reports no user, leave the card blocked, ask once in text with the same choices, and end the turn; never pick an option yourself, whatever the timeout message says.

In a background turn (cron output, no app attached) the plugin catches `clarify`: it saves the question, blocks the card, and Herm's notch shows the choices. Always name the card id (`t_…`) in the question. When the reply says the question is queued, end the turn with one line. The user's tap arrives later as `ANSWER <id>: <choice>`: comment the answer on the card, unblock it, and apply it like `continue <id> <answer>`.

## Board triage replies

The `Board triage & reminders` cron (every 20m, digest once a day or when a new decision is needed) retries cards that only hit a rate limit, timeout, or worker crash and lists the rest; Mission Control in the dashboard shows the same groups with buttons. When the operator replies to that digest:
- `continue <id> [instruction]`: read the card and its latest comments. If the block was an old rule or a broken workspace (empty scratch, no node_modules, OCR, install stop), resubmit instead. Otherwise record the instruction as the decision: `hermes kanban unblock <id> --reason "<instruction>"` (blocked/scheduled) or comment it and `hermes kanban promote <id>` (todo). A triage card needs the instruction in its body first: comment it, then `hermes kanban specify <id>`.
- `archive <id>`: operator-directed; comment one line of why, then `hermes kanban archive <id>`.
- `resubmit <id>`: write a brief from the card (goal, acceptance criteria, named files, under 3000 chars), call `delivery_submit` with the card's project, then comment `Resubmitted as <new ids>` on the old card and archive it.
Several ids in one reply apply the same action to each. Report one line per card.

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
