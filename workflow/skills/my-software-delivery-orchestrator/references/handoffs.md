# Bot and Kanban Handoffs

Use this brief before handing work to a Bot, creating a Kanban card, or requesting an independent gate. The receiving Bot must be able to act without parent-chat history.

```text
TASK_ID:
OBJECTIVE:
SCOPE:
NON-GOALS:
INPUTS:

ROUTING
- primary Bot profile ID:
- execution backend: direct | bot_chat | profile_invocation | delegate_task | kanban
- observed provider/model or unverified:

ORIGIN DELIVERY
- originating session/platform/chat/thread or explicit subscription identity:
- notify-list verification:
- delivery mode:

STATE AND PREFLIGHT
- exact repository/worktree/branch/base:
- clean state and unrelated work to preserve:
- project instructions:
- dependency readiness:
- focused-test baseline and natural teardown result:
- required profile, skill, OCR, and gate readiness:

EXECUTION CONTRACT
- exact write allowlist:
- acceptance criteria and commands:
- one commit boundary:
- idempotency key:
- max_runtime:
- native lifecycle transition expected at completion:
- required Sentry/Sentinel/Cypher/Aegis gates:
- BUDGET:
- stop conditions:

APPROVALS
- allowed local actions:
- prohibited actions:
- Christian approval required before:

ACTIVE SECURITY ENGAGEMENT — required only for Wraith
- originating Cypher finding ID:
- ownership/authorization basis and approver:
- exact target allowlist:
- environment and production-in-scope state:
- engagement start/end window:
- permitted techniques:
- prohibited techniques:
- per-host rate limit:
- test credentials/data-handling rule:
- evidence path and redaction rule:
- stop conditions:
- destructive testing approval:
- production testing approval:

DELIVERABLE:
RETURN_TYPE:
NEXT OWNER/CHECKPOINT:
```

## Result envelope

```markdown
## Bot and placement
- profile:
- execution backend:
- actual provider/model or unverified:

## Status
PASS | NEEDS_CHANGES | NEEDS_ASSISTANCE | RETRYABLE | HARD_BLOCKED

## Scope and evidence
- files/state/actions:
- acceptance criterion → command or inspectable evidence → result:

## Findings and residual risk
- stable finding ID, severity, evidence, smallest recommendation, owning Bot:

## Handoff
- next Bot or Nexus action:
- approvals or missing evidence:

## Result projection
- latest event:
- current/latest run outcome:
- structured verdict metadata:
- next executable owner/state:
```

Do not dispatch a material implementation card with an unresolved preflight field. Split the card before dispatch if it includes independent modules, environment setup, a new harness, unresolved policy, or multiple commits. Require phase heartbeats with elapsed time and current command/result.

Worker summaries are evidence candidates. Nexus verifies canonical state before reporting completion. For multi-stage or restart-sensitive work, put the handoff in Kanban comments, completion metadata, or attachments—not only in Bot messages.