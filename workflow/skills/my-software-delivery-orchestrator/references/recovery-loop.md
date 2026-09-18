# Autonomous Recovery Loop

Authoritative source for canonical envelopes, hard stops, recovery packets, convergence, and resumption. A blocker is normally a routing event, not completion.

```text
problem → lead verifies/classifies → narrow investigator/fix
→ lead verifies new state → originating gate resumes → invalidated gates rerun
```

Leaf workers return evidence to the Lead; they never dispatch help, contact the user/peers, or self-remediate and approve.

## Envelopes

| Status | Meaning | Lead route |
| --- | --- | --- |
| `PASS` | Assigned checkpoint met with evidence | Advance. |
| `NEEDS_CHANGES` | In-scope known/bounded defect | Separate bounded fix, then affected gates. |
| `NEEDS_ASSISTANCE` | Missing expertise/context/tool or unresolved technical obstacle | Narrow Investigator/Specialist, then resume originator. |
| `RETRYABLE` | Plausibly transient tool/process/provider/environment failure | Verify state; bounded backoff or distinct supported path. |
| `HARD_BLOCKED` | User-only decision/new authority, unavailable required access/credentials, unsafe action, inaccessible external dependency without faithful authorized substitute, audited exhaustion of all distinct safe strategies, or exact observed runtime/resource ceiling | Continue safe independent work; report `BLOCKED/INCOMPLETE` with audit and resume condition. |

Raw `FAIL`, `BLOCKED`, `REQUEST_CHANGES`, and `NOT_READY` are role verdicts, never envelopes. Normalize them by observed cause. QA/review/security/build failures are recoverable by default.

## Recovery request

Every non-pass includes:

```text
blocker/finding ID and origin role
canonical status and failing state
exact command/action/output and affected scope
diagnosis or ranked hypotheses
attempts/outcomes and strategy fingerprint
requested specialist/capability
safe next probe/fix
user input/permission: none | exact requirement
resumable checkpoint
```

`HARD_BLOCKED` additionally lists every distinct attempted strategy; every plausible remaining strategy and why unsafe/unauthorized/duplicate/inapplicable; exact observed ceiling when applicable; safe work completed; and precise resume condition. If this evidence is absent, investigate before accepting the hard stop.

## Classification

- **Known product/code defect:** for an ordinary same-card review finding, preserve the card and return it through native `request_changes` → focused checks → fresh review. Create a separate bounded finding-fix only when the correction is independently reviewable, needs another workspace/owner, or exceeds the original boundary.
- **Unknown technical problem:** narrow Investigator/Specialist → lead validates diagnosis → separate fix if needed → originating gate.
- **Tool/environment:** deterministic tools or `Investigator / debugging` / `Implementer / devops`; distinguish repository fix, safe local prerequisite, transient fault, baseline limitation, and credential/access requirement. Alternative checks must verify the same criterion.
- **Material requirement ambiguity:** return to G-1; Lead asks the smallest user question. Facts may be gathered read-only, but no worker chooses product intent.
- **Clear intent, architectural uncertainty:** Planner/Investigator determines the technical route.
- **Permission/safety/access/external dependency:** hard stop only when no faithful safe authorized route exists. Preserve unrelated work and do not retry/bypass denied or timed-out approval.

## Cycle

1. Register stable ID, origin, state, and checkpoint.
2. Verify evidence against canonical repository/environment state.
3. Classify and deduplicate.
4. Route the narrowest fresh helper; investigate before editing when cause is unclear.
5. Fix only accepted in-scope findings within authority.
6. Run focused red-capable checks and inspect collateral changes.
7. Resume the originating role on the updated frozen state with diagnosis, changed files, evidence, and checkpoint.
8. Rerun every invalidated gate.
9. Close only on fresh independent evidence.

A fresh same-role process may resume; preserve the ledger and rejected hypotheses.

## Terminal-event projection

On every terminal notification or wake, Coordinator reads the lifecycle event, latest run outcome and structured metadata, summary, comments, and dependencies before reporting a state change. Review metadata takes precedence over a generic task result: `request_changes` means correction required and fresh review; `approved` advances only after the remaining exact-state gates pass; missing evidence or a missing successor is `NEEDS_ASSISTANCE` or `HARD_BLOCKED` according to its cause.

## Convergence

Persist only while a cycle adds evidence, narrows hypotheses, changes implementation/environment, or uses a genuinely distinct strategy. Never repeat an identical command/fix with unchanged prerequisites.

After two ineffective cycles, rotate investigator, skill, or test strategy; model-tier rotation requires a verified backend because native delegation shares placement. After three failed fix strategies for one root cause, run fresh architecture/root-cause reassessment. Continue when it finds a safe in-scope path. Stop only with audited `HARD_BLOCKED` or explicit user instruction.

Maintain a compact ledger: ID, origin, class, owner, state, strategy, evidence, status, resume target. Recovery completes when recoverable blockers close and invalidated gates pass on the final state; remaining `HARD_BLOCKED` always means blocked/incomplete.
