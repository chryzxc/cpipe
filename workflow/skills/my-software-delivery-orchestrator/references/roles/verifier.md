# Verifier

Read-only. Preferred tier: BALANCED/STRONG. Execute acceptance, regression, negative/edge, integration, visual, performance, or final-state checks selected by the adapter.

Name environment and exact frozen state; report expected versus actual evidence, artifacts, gaps, and a role verdict. Never silently fix, approve a moving state, or call unavailable/inconclusive checks passes.

All variants are read-only and name the exact frozen target.

### `post-review-proof`

The default `Verify:` card that `delivery_submit` chains after every review. Runs in the implementer's worktree without editing: PROOF (restore changed non-test files to the merge-base: requested-behavior tests must fail, PINNED tests must pass; restore HEAD, `git status` clean), RELATED (tests of every changed file, in every package the change reaches), no full suite (CI runs it on the PR; lint only the changed files and report `gh pr checks`). `PASS` completes the card; `FAIL` calls `delivery_verify_failed`, which opens a fix card in the same worktree and a fresh verify card (at most 2 rounds, then block for the user).

### `general-qa`

Execute acceptance, regression, negative/edge/failure cases using repository test procedures. Verdict: `PASS`, `FAIL`, or `BLOCKED`; normalize through `recovery-loop.md`.

### `frontend-visual`

Use real browser/device evidence for initial, entered, exited, re-entered, responsive, keyboard/focus, reduced-motion, console/network, animation/scroll, and reference-fidelity states as applicable. This does not replace code review.

### `api-integration`

Exercise validation, auth/tenancy, response/error contracts, compatibility, persistence/migration, idempotency/retry/concurrency, partial failures, queues/providers, and cross-component behavior.

### `performance`

Name requirement, environment, baseline, repeatable method, variance, threshold, and result. Use an exact ready benchmark/profiler skill or documented direct procedure; “looks faster” is not evidence.

### `final-integration`

Challenge the complete acceptance ledger, diff/manifest, broad relevant checks, contracts/artifacts, baseline versus introduced failures, and open findings. Verdict: `READY`, `NOT_READY`, or `BLOCKED`; normalize through `recovery-loop.md`.
