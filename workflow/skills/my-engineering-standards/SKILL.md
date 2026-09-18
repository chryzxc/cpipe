---
name: my-engineering-standards
description: "Scaffold and enforce a per-repo STANDARDS.md contract."
version: 0.1.0
---

# Engineering Standards Skill

Every production repo carries one canonical `STANDARDS.md` at its root: the short, checkable engineering contract the whole Bot roster plans against (Planner), follows (Implementer), and enforces (Reviewer). Standards live in the repo, not in Bot memory, so they version with the code they govern.

## When to Use

- First material change to a repo lacking `STANDARDS.md` → scaffold it as part of that change (not a separate chore).
- Plan/review work in a repo that has one → read it before designing or judging.
- A standard changes → edit `STANDARDS.md` in the same card that changes the code, like the repo map.

## Scaffold (hard cap: 150 lines)

```markdown
# <repo> — Engineering Standards
updated: <date> · enforced-by: Reviewer review · exceptions: none without an ADR

## Style & naming        — language conventions, naming rules, import order
## Error handling        — error taxonomy, propagation rules, user-facing messages
## Testing               — pyramid shape, what each layer must prove, focused vs full commands, coverage floors
## Definition of Done    — gates a card passes before COMMIT_READY (tests, lint, docs, evidence format)
## Boundaries            — layering rules: what may import/call what; forbidden dependencies
## Git & commits         — Conventional Commits scope, one-behavior-one-commit, branch naming
## Observability         — logging levels/metadata, metrics, health signals new services must emit
## Security baseline     — input validation, secret handling, authz checks, dependency policy
## Exceptions            — recorded deviations, each with ADR reference and expiry
```

## Enforcement contract

- Implementer: `PLAN_AMENDMENT_REQUIRED` when a plan's slice violates a standard — never implement through a violation.
- Reviewer: findings cite the standard by section (`STANDARDS.md §Testing`), not personal taste; a diff that breaks a standard is `REQUEST_CHANGES` with the section quoted.
- A standard with no checkable signal is deleted at the next edit — rules that cannot be cited cannot be enforced.
- Exceptions expire; an expired exception becomes a finding.

## Non-goals

Tutorial content, framework docs duplication, anything the linter/formatter already enforces mechanically (point at the tool instead).
