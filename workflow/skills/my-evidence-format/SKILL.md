---
name: my-evidence-format
description: "Emit terse machine-parseable evidence in every bot output."
version: 0.1.0
---

# Evidence Format Skill

One terse evidence contract for every Bot's reports, reviews, heartbeats, and findings. Prose is where tokens bleed; this format replaces it.

## When to Use

Always, when a Bot reports results, findings, phase receipts, or verdicts in the Bot-first workflow. Not for user-facing conversation — only for Bot-to-Nexus and Bot-to-reviewer evidence.

## Finding entry

One line per finding, fields separated by ` · `:

```
<ID> · <severity> · <title>
Evidence: <file>:<line> | <command> | <verbatim output line or SHA>
Impact: <measured or estimated effect>
Fix: <smallest correction>
Owner: <profile> · Effort: <S|M|L> · Verify: <command + expected signal>
```

Rules:
- `ID` is stable across the whole task (PERF-###, SEC-###, QA-###, REV-###).
- `Evidence` is verbatim — a real file:line, a real command, a real output line or commit SHA. Never a paraphrase.
- `Impact` carries a number when one exists (ms, bytes, req/h, tokens, $); "significant" is not an impact.
- `Fix` names the smallest correction, not a redesign.
- A suspicion with no evidence line is labeled `HYPOTHESIS` and never counts in verdicts.

## Phase receipts

```
<PHASE> · elapsed <mm:ss> · <current command/result>
```

## Verdicts

Single line: `VERDICT: <APPROVED|REQUEST_CHANGES|NEEDS_ASSISTANCE|READY|READY_WITH_RISK|BLOCKED|SPIKE_VERDICT...> · <one-line reason>`

## Non-goals

No narrative paragraphs, no restating the task, no repeating tool output beyond the cited line, no markdown decoration around entries.
