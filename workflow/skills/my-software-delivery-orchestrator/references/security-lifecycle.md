# Security Delivery Lifecycle

This reference governs security work inside the software-delivery control plane. It separates read-only analysis, authorized active validation, remediation, independent review, regression evidence, and closure.

## Lanes

1. **Read-only audit — Security Reviewer.** Analyze source, configuration, dependencies, trust boundaries, credentials, auth, privacy, and reachability without active target testing.
2. **Active validation — Security Tester.** Validate one specific, evidence-backed hypothesis only after the complete engagement receipt is authorized.
3. **Remediation — Implementer.** Implement one bounded fix from a confirmed or actionable Security Reviewer finding.
4. **Independent review — Reviewer.** Review the frozen remediation diff on the same card.
5. **Regression evidence — Verifier.** Run triggered runtime, integration, visual, or release evidence on the new frozen state.
6. **Security closure — Security Reviewer.** Reassess the original finding ID against current evidence and state residual risk.

## Security Tester admission gate

A card is not ready until every field below has a concrete value:

- authorization basis and approver;
- originating Security Reviewer finding ID;
- exact hostname, IP, or CIDR allowlist;
- explicit environment and whether production is included;
- engagement start and end window;
- permitted techniques;
- prohibited techniques;
- per-host rate limit;
- test credentials and handling rule, or `none`;
- evidence storage path and redaction rule;
- stop conditions;
- destructive-action approval state;
- production-testing approval state.

`not applicable` is valid only for test credentials and destructive/production approval when those activities are explicitly prohibited. A missing field keeps the card `BLOCKED`.

## Required execution contract

The originating Security Reviewer finding must state the hypothesis, reachability rationale, smallest witness needed, expected evidence, and why static validation is insufficient. the operator’s approval must name the target, environment, time window, permitted techniques, and safety ceiling. Coordinator verifies the Security Tester profile, its exact profile-local skills, current model/provider placement, clean integration state, actual `max_runtime`, idempotency key, evidence workspace, and source subscription before dispatch.

Security Tester validates; it does not assign final severity, define remediation, edit application code, or approve closure. The result returns to Security Reviewer for interpretation.

## Safety stops

Stop immediately and return `BLOCKED` when authorization is missing or expired, a host or redirect is outside the allowlist, the window expires, the required technique is prohibited, credentials or sensitive data may leak, production data impact appears, sustained 429/503 responses indicate service stress, the evidence budget is exhausted, or the scope expands. Do not continue after a stop condition to collect a nicer reproduction.

## Verdicts

- **CONFIRMED** — the minimum authorized witness changed behavior and the evidence is reproducible.
- **NOT_REPRODUCIBLE** — the authorized test set was exhausted with no witness.
- **PARTIAL** — a sink or reachability path is shown but exploit impact is not established.
- **BLOCKED** — scope, authorization, environment, credentials, safety, or evidence requirements are missing.

## Return path

Security Tester returns to Coordinator with the engagement ID, originating finding ID, authorization/scope receipt, observed profile/model, exact tools and commands, verdict, redacted reproducible evidence, tested and untested paths, limitations, residual risk, and next owner `Security Reviewer`. Security Reviewer interprets significance and defines remediation. Implementer owns a bounded fix. Reviewer reviews the frozen fix. Verifier runs triggered regression evidence. Security Reviewer closes or reopens the original finding after the new state is verified.

## Evidence handling

Store only approved, redacted evidence in the engagement workspace. Never include secrets, session tokens, personal data, or unapproved target data in card bodies, comments, logs, or final reports. Preserve the exact target, timestamps, tool versions, request/response witness, and stop reason needed for independent review.
