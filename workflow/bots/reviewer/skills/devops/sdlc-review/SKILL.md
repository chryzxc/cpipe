---
name: sdlc-review
description: Review Kanban handoffs and route verified outcomes.
version: 1.1.0-cpipe
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [kanban, review, quality, verification]
    category: devops
    requires_toolsets: [kanban]
environments:
  - kanban
---

# SDLC Review (cpipe)

The dispatcher loads this for every review-lane card. The card's REVIEWER (or CONTENT CHANGE)
section is the procedure; this only adds the lifecycle.

1. `kanban_show` first: the brief, the latest `review_requested` handoff, prior review rounds.
   The handoff is a claim to verify, not evidence.
2. Re-review: if the patch is unchanged since the last reviewed commit (a rebase or an empty
   push), the prior verdict stands. Check with
   `git diff $(git merge-base origin/<base> <sha>) <sha> | git patch-id --stable` for the last
   reviewed sha and for HEAD; equal ids = same patch. Re-run only the related tests, then repeat
   the prior verdict.
3. Exactly one terminal action, with the checks you ran as evidence:
   - approve: `kanban_complete`
   - correctable defects: `kanban_request_changes` (each finding: file:line, defect, smallest fix)
   - a human decision only the user can make: `kanban_block`
4. Never edit the implementation; the implementer fixes and requests review again.
