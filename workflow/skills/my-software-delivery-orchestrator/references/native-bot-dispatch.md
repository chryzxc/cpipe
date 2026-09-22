# Native Hermes Bot Dispatch

## Canonical model

A Hermes Bot is a named **profile**, not an abstract role card or a `delegate_task` child. For material work, Coordinator routes to one verified profile through a manual Kanban card.

```text
the operator request
  → Coordinator preflight and Bot selection
  → explicit Kanban card with verified assignee + task skill(s)
  → Hermes dispatcher starts the assigned profile worker
  → worker updates canonical Kanban state/evidence
  → independent Bot gates review a frozen state
  → Coordinator verifies evidence and reports to the operator
```

Keep this orchestrator skill on Coordinator. Keep each Bot's mandate in its profile `SOUL.md` and its procedure in the profile-local role skill. Do not install the control-plane skill into specialist profiles and do not create a second role registry.

## Preflight checklist

Before a Bot task is created:

1. Choose exactly one primary Bot using `routing.md`.
2. Verify the profile and its description:
   ```bash
   hermes profile list
   hermes profile describe <profile>
   hermes profile show <profile>
   ```
3. Verify each forced task skill is installed on that exact profile:
   ```bash
   hermes -p <profile> skills list
   ```
4. Record the profile's observed model/provider as observed placement; do not infer a provider or use a `delegate_task` override.
5. Establish the exact repository/worktree/branch/base, clean state, dependency readiness, focused-test baseline, exact write allowlist, one behavior and commit boundary, acceptance commands, actual `max_runtime`, stop conditions, and required gates from `handoffs.md`.
6. Block readiness work separately. Do not hide dependency installation, a new harness, reviewer provisioning, OCR setup, or test-health repair inside implementation.
7. For Security Tester, require the complete security engagement receipt before a card can be unblocked: originating Security Reviewer finding, authorization basis/approver, target allowlist, environment, window, techniques, rate limit, credentials/data handling, evidence path/redaction, stop conditions, destructive approval, and production approval. Verify the `security-tester` profile, `web-pentest` and `my-evidence-format` readiness, exact workspace, no inherited credentials, actual `max_runtime`, idempotency key, and source subscription. Any missing field keeps the card blocked.
8. Establish originating session/platform provenance or an explicit subscription identity, an idempotency key, and the next expected local lifecycle transition. Pre-create only distinct known QA/security/operations gates as blocked dependencies; dispatch only the current executable slice.

## Dispatch choice

| Work shape | Native route | Why |
| --- | --- | --- |
| Deterministic read or one command | Coordinator tools | No specialist lifecycle required. |
| Brief specialist question | Bot Chat or `hermes -p <profile> chat -q` | Synchronous; not durable. |
| Research, design, implementation, operations, QA, review, or security work needing evidence/recovery | Kanban assigned to profile | Worker receives its profile SOUL, model, skills, memory, and a durable task context. |
| Small internal reasoning with no named-identity requirement | `delegate_task` | Ephemeral only; never represent it as a Bot. |

## Manual Kanban lifecycle

The Coordinator configuration uses `kanban.auto_decompose: false`; route deliberately rather than relying on the auxiliary decomposer.

### Create

```bash
hermes kanban create "<precise task title>" \
  --assignee <verified-profile-id> \
  --body "<complete handoff brief>" \
  --workspace "dir:<approved-workspace>" \
  --skill <verified-profile-local-skill> \
  --priority <priority> \
  --idempotency-key <stable-key> \
  --max-runtime <bounded-duration> \
  --completion-contract local-only \
  --json
```

- Repeat `--skill` only for separately verified skills installed on the assignee profile.
- Use `--project` and a deterministic `--branch` only when the task is explicitly authorized to work in that project/worktree.
- Do not pass `--model` / `--provider` unless live preflight proves the override is valid and the changed placement is authorized.
- Use `--goal` only when the card is genuinely open-ended and its completion criteria are judgeable from the card body.
- Native session-aware creation is preferred. If CLI creation is required, immediately add an explicit `notify+wake` subscription for the originating source and prove it with `hermes kanban notify-list <task-id> --json` before leaving the card unattended.
- The creation receipt must record source/subscription provenance, `notify-list` verification, idempotency key, actual `max_runtime`, readiness receipt, one owner, and next local transition.
- Do not substitute a guessed chat ID, platform, thread, or notifier profile. A task without a real originating session must remain attended or be blocked for an explicit destination; it cannot truthfully promise autonomous continuation.

### Terminal-event continuation contract

The dispatcher only claims cards that are already `ready`; it does **not** interpret worker evidence, request review, create a correction, or promote a child. The gateway notification is the handoff to Coordinator, not the successor itself.

For each material card, Coordinator must subscribe the real originating source with `--delivery-mode notify+wake`, using the exact platform/chat/thread/user identity and owning notifier profile recorded at creation. After every terminal wake, Coordinator reads `show`, `runs`, `context`, and `notify-list`, then records exactly one of these decisions in the canonical card comment:

1. Implementer result → request same-card Reviewer review with the frozen Git range and evidence.
2. Reviewer `REQUEST_CHANGES` → native same-card recovery to Implementer; fresh review is required after any head change.
3. Approved result → promote exactly one dependency-ready, distinct QA/security/operations child gate, or create one bounded next packet with fresh preflight and a new verified subscription.
4. Failed, timed-out, crashed, or insufficient-evidence result → retain/create a named blocker with an owner and notify the operator; never silently create retry loops.
5. All acceptance criteria and required gates verified → final-report the result and stop.

The decision is recorded as a comment starting with the literal tag `CONTINUATION:` followed by the chosen decision (`same-card correction`, `promote gate <card-id>`, `successor <card-id>`, `blocker <name> (owner <profile>)`, or `final report`) plus evidence ids/URLs. Replayed terminal notifications are no-ops when the marker already exists after the terminal event. Before any manual promote or dispatch, verify canonical status, workspace validity, and that no newer card supersedes the same scope — record supersession as a comment linking the replacement card-id.

A terminal card without a recorded continuation decision is an orphaned chain. Do not describe the workflow as autonomous until both the task subscription and one terminal-event-to-decision cycle are observed.

Supervisor readiness signals change the recovery inputs, never the loop count. `READINESS_BLOCKER` (profile/provider failed to authenticate or start) requires authenticating the named profile before any re-dispatch — the card stays blocked until then. `WORKER_EMPTY_RESULT` (terminal run with no usable response, e.g. a goal-mode worker that exhausted its turns) is recorded as a distinct outcome and routed to exactly one bounded recovery decision. `RESPAWN_LOOP` (engine failure breaker tripped) forbids further respawn. Each decision preserves the exact workspace the scan reported (path@sha), current commit/status, and the dependency graph, and comments its marker (`readiness_blocker`, `worker_empty_result`, `respawn_bounded`) so later supervisor ticks stay silent.

### Observe and recover

```bash
hermes kanban context <task-id>
hermes kanban show <task-id> --json
hermes kanban runs <task-id> --json
hermes kanban dispatch --dry-run --max <n> --json
```

Use the live gateway dispatcher for actual dispatch. Do not manually launch an idle Bot gateway solely to process a Kanban card. If a required task is blocked, retain canonical evidence and use the recovery loop; do not silently substitute another Bot.

### Independent gates

After an implementer freezes a meaningful code change:

1. Implementer requests same-card independent review from **Reviewer** with the exact frozen diff, changed files, test evidence, and required OCR evidence.
2. Reviewer approves by completing the review state, returns a confirmed in-scope defect through `request_changes`, or blocks for missing independent evidence. A changed head requires fresh review.
3. Route regression/release evidence to **Verifier** when triggered and trust-boundary, credential, auth, privacy, or plausible vulnerability work to **Security Reviewer**; these remain distinct linked cards when required.
4. Create a separate correction card only when a finding is a distinct independently reviewable behavior, needs another workspace or owner, or exceeds the original one-commit boundary.

The original implementer never self-approves. Gateway state, a profile's existence, a green build, or a worker summary is not final evidence by itself.

## Completion record

Coordinator reports:

- Bot profile and actual route (`bot_chat`, `profile_invocation`, or `kanban`);
- observed profile model/provider and any verified override;
- task ID(s), canonical status, and scope;
- acceptance criterion → current evidence → outcome;
- executed independent gates, findings, residual risks, and explicit blockers;
- approvals still required from the operator.
