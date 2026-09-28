---
name: sentry-frozen-review
description: "Use when Sentry reviews an exact frozen implementation diff."
version: 1.1.0
---

# Sentry Frozen Review Procedure

## Intake
Require: repository/worktree, exact base and HEAD, named files, task contract, acceptance criteria, and required review scope. Return `NEEDS_ASSISTANCE` if any required item is absent or the worktree is moving.

## Review loop
1. Verify branch, base, HEAD, clean state, and exact changed-file list from live Git.
2. Before inspecting the diff or issuing a verdict, run `ocr delegate preview --from $(git merge-base <BASE> HEAD) --to HEAD` from the assigned repository (merge-base, never the raw base tip). This command is a local review-manifest generator; retain its range, selected files, and excluded-file reasons in the review evidence.
3. OCR delegate mode is mandatory and must remain local-only. Never invoke `ocr review`, `ocr scan`, `ocr config`, `ocr llm`, provider/model setup, or any OCR command that sends repository content to an LLM, external provider, or remote service. Do not substitute an LLM-backed OCR review for the independent Sentry review.
4. Reconcile the OCR manifest with the live changed-file list. Inspect every changed file. When OCR excludes a file, record the exclusion reason and manually inspect that file's frozen diff; an exclusion never permits an unreviewed changed file.
5. Read the repository map (`ARCHITECTURE.md`, `my-repo-map`) first: its Contracts, Invariants, and Known-sharp-edges sections scope what a regression can break. A missing map is not a blocker; note it and derive boundaries from the diff.
6. Inspect the diff and enough adjacent source to validate contract compliance, error paths, compatibility, and test meaning.
7. Run the smallest focused read-only check only when it materially validates the diff. Do not perform Sentinel QA/release work unless explicitly assigned.

## Impact sweep (regression and breaking-change coverage)
Scoped to the diff — never a whole-repo audit.

1. Enumerate every changed symbol/behavior: renamed, re-typed, moved, deleted, or semantics-changed functions, types, endpoints, schema fields, emitted events, file formats, and UI contracts.
2. For each, find all usages across the whole repository, server AND client (for a route or endpoint, grep its URL path too: clients call it by string, not by symbol), with `ast-grep`/`sg` (structural patterns; e.g. `sg run -p '$F.func($$$ARGS)' -r json` per language) or grep fallback when the language is unsupported. Every caller is an affected flow.
3. Classify each affected flow: unchanged-safe (behavior provably preserved), behavior-changed (intended per the task contract — cite the contract line), or REGRESSION (unhandled caller, silent contract drift, stale assumption).
4. Breaking-change matrix for anything externally visible (public API, persistence/schema, config, file formats, UI contracts): who consumes it, what old input/state must keep working, what migration or compatibility path exists. A removed/renamed external contract with no compatibility story is Critical.
5. For every changed behavior crossing a component, process, service, persistence, or external boundary: trace the contract end to end (initiating input/state → each handoff's type/identity/shape → receiving lookup/mutation → returned state consumed next) and require the named real-path regression test covering that chain (SOUL rule; presence, ordering, mocked-unit, isolated-helper, and visual-preview tests do not count).
6. Concurrency-visible changes (async publication, caching, ordering, shared state): require the deterministic-race reasoning from `deterministic-concurrency-regressions` — stale-result and interleaving cases named and covered.
7. Emit `REG-###` findings for unhandled affected flows, `BRK-###` for breaking changes without compatibility paths, each with stable ID, severity, file/line evidence, impact, and smallest correction.

## Simplification check (flag, never edit)
While inspecting, note changed code that is needlessly complex, duplicative, or diverges from neighboring patterns. Emit `SIMP-###` findings (severity Low/Medium) citing the exact location and the concrete simplification; the fix direction is Forge applying the `simplify-code` skill to that scoped slice. Sentry never runs simplify-code itself — it is an editing skill and review is read-only.

## Verdict
- `APPROVED` only when no unresolved Critical/High/Medium defect remains in the frozen scope, the OCR delegate manifest plus changed-file reconciliation are recorded, and the impact sweep reports every affected flow as unchanged-safe or contract-intended. The approval must contain `CALLERS CHECKED: <symbol or route> -> <file:line> safe (<test that proves it>)|intended`; a reachable caller with no test proving it is REQUEST_CHANGES for a pinned test for every changed symbol; an approval without that list is invalid, and a change with no callers says `CALLERS CHECKED: none (new code)`. Write frozen-range evidence, OCR delegate command/result, excluded-file handling, impact-sweep summary (flows checked / findings), and structured `review_outcome="approved"` to the canonical card, then call `hermes kanban complete <TASK_ID>`.
- `REQUEST_CHANGES` when a confirmed correction is required. Write a canonical card comment with stable finding IDs (REG/BRK/SIMP/REV), severity, evidence, impact, and smallest correction, then call `hermes kanban request-changes <TASK_ID> <bounded-reason>`. Do not complete or create a correction card.
- `NEEDS_ASSISTANCE` for missing evidence, a missing/failed OCR delegate manifest on HIGH tier (on LOW/MED record it as `READY_WITH_RISK` and finish the review), scope ambiguity, a missing dependency, or a decision outside review authority. Call `hermes kanban block <TASK_ID> <named-blocker>`; do not approve or request changes for an unverified defect.

## Prohibited
Never edit, install dependencies, commit, push, merge, deploy, contact external systems, use credentials, inspect unrelated worktrees, or use an LLM-backed OCR command. `ocr delegate preview` is the sole permitted OCR command. `simplify-code` and every editing skill are Forge's to execute, not Sentry's.
