---
name: my-repo-map
description: "Maintain and read a canonical repo map before discovery."
version: 0.1.0
---

# Repo Map Skill

A canonical, bounded repository map (`ARCHITECTURE.md` at the repo root) that every planning and review pass reads FIRST instead of re-discovering the codebase with fresh greps. Re-discovery is the largest recurring token cost in the workflow; the map amortizes it.

## When to Use

- Archon: read the map before any plan; create or update it when missing or stale (first planning act, not a separate task).
- Forge: read the map for orientation; update the affected sections in the same card that changes them.
- Sentry/Sentinel/Cypher: read the map to locate contracts and boundaries for review scoping.
- Never as a substitute for verifying the actual frozen diff — the map orients; the diff decides.

## Map format (hard cap: 200 lines)

```markdown
# <repo> — Repo Map
updated: <date> · head: <short SHA>

## Entry points      — binaries, apps, servers, CLIs; one line each
## Modules           — top-level dirs; one line: purpose + key files + owner-ish boundary
## Data flow         — input → processing → persistence → output, one line per path
## Contracts         — cross-boundary interfaces (API, schema, IPC, file formats); one line each
## Persistence       — databases, stores, migrations; one line each
## Test map          — where tests live per module; how to run focused vs full
## Invariants        — rules that must never break (auth, tenancy, money, ordering)
## Known sharp edges — surprising behavior, debt zones, do-not-touch areas
```

## Update rules

1. Update the affected sections in the same card that changes them — never a map-only follow-up card.
2. Every line must stay verifiable: if a line can't be traced to code, delete or fix it.
3. `updated:` + `head:` must change on every edit; readers treat a stale map (head not an ancestor of the frozen base) as a hint, and verify against the diff.
4. Cap discipline: when a section exceeds its usefulness, compress — the map is an index, not documentation.

## Non-goals

No design rationale (that lives in ADRs), no task history, no secrets or environment detail, no duplicating what README already covers in one glance.
