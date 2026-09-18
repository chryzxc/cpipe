# Pull Request Delivery

Load this reference only after **both** activation conditions hold:

1. the user explicitly authorized `CREATE_PR`; and
2. every required final local/independent gate passed on the canonical state.

A possible future PR, issue-driven task, “prepare a PR,” ordinary bug fix, local commit request, or early `CREATE_PR` authorization while gates remain open does not activate this workflow. The lead may retain authorization while finishing gates, but must not spawn PR Delivery, initialize/freeze a dossier, or perform delivery-only remote work yet.

## Boundary

The `PR Delivery` base role validates and publishes already-verified work. Within `CREATE_PR`, it may prepare necessary authorized local commits, push the authorized head branch, and create/update the intended PR. It may not repair production code, broaden scope, merge, deploy, rewrite history, request reviewers, label, comment, change security settings/credentials, or perform release actions without separate explicit authority.

PR delivery and causal-history analysis are separate capabilities. Delivery never initiates `git blame`, bisect, regression-range analysis, author attribution, or speculative searches for related PRs/issues merely to make the description look detailed.

## Lazy dossier assembly

Before activation, ordinary roles return ordinary evidence: clarified acceptance, root cause, changed files, decisions, test results, findings, risks, and final verdicts. They do not format a PR dossier in advance.

After activation, the lead assembles one concise source-linked dossier from those verified handoffs and current repository state. Missing material delivery evidence is gathered narrowly; do not rerun every role merely for PR formatting. `Verifier / final-integration` conflict-checks the dossier against the canonical state. PR Delivery validates rather than invents it.

```text
PR EVIDENCE DOSSIER
- repository / base / head / canonical revision:
- explicit CREATE_PR authorization and prohibited actions:
- user outcome, non-goals, acceptance ledger, issue linkage:
- symptom and evidence-backed root cause when this is a bug fix:
- changed behavior/contracts and implementation decisions:
- changed-file and branch-commit manifest:
- closed correctness/MAINT-N findings and final review state:
- QA/gate commands, observed results, and environment:
- triggered security/performance/data/platform evidence:
- compatibility, migration, rollout, rollback, observability:
- known limitations, baseline failures, unavailable checks, residual risks:
- verified screenshots/artifacts when useful:
- reviewer reading order/hotspots:
- causal provenance: NOT_RUN | CONFIRMED | LIKELY | UNKNOWN | N/A
- provenance trigger/question/method/evidence, only when not NOT_RUN:
- included related commits/PRs/issues, only when verified and useful:
```

`NOT_RUN` is the default for causal provenance. Omit irrelevant sections from the PR rather than padding them. Never include secrets, credentials, private customer data, raw agent chatter, irrelevant logs, personal blame, or unsupported claims.

## Optional causal provenance

A bug fix or PR is **not** a history trigger. Causal history may exist in the dossier only when an earlier lead/investigator recorded one of these concrete triggers:

- the user explicitly requested provenance;
- current-state reproduction/code/runtime evidence could not answer a concrete regression-range or root-cause question;
- rollback, compatibility, release, or material risk analysis depended on identifying a change/range.

Before any provenance work, record:

```text
question:
expected decision affected by the answer:
why current-state evidence is insufficient:
least expensive read-only history evidence allowed:
```

Use the least expensive targeted evidence. `git show`/targeted log may be enough. `git blame` and bisect are exceptional, never mandatory, and should run only when they directly answer the recorded question at justified cost. Blame identifies line history, not causality. Do not attribute fault or intent to a person.

Statuses:

- **NOT_RUN:** no trigger; no causal-history analysis performed.
- **CONFIRMED:** before/after, bisect, or equivalent reproducible evidence proves the named commit/range changed passing behavior to failing behavior.
- **LIKELY:** targeted history plus root-cause evidence strongly supports the relation without reproducible proof.
- **UNKNOWN:** triggered analysis ran but available evidence is insufficient, ambiguous, inaccessible, shallow, or rewritten.
- **N/A:** analysis established that this is not a regression or commit provenance is irrelevant.

Only `CONFIRMED` supports “introduced by” or “caused by.” Use calibrated wording for `LIKELY`; describe inconclusive checks for `UNKNOWN`. PR Delivery verifies already-included provenance evidence but does not start or expand the investigation.

## Required inputs

The lead supplies:

- repository path and project instructions;
- explicit `CREATE_PR` authorization;
- canonical branch/revision, verified base, and scope;
- successful final gate ledger and frozen dossier;
- requested draft state and any separately authorized metadata;
- prohibited actions and unrelated work to preserve.

Load `creating-pr-content` for the normative title/body/metadata contract. Load `github-pr-workflow` only when provider-specific GitHub commands are needed, plus the repository PR template and issue workflow when applicable. Repository presentation requirements may rename or combine headings; generic examples never override project base, rules, authority, or the verified content requirements.

## Delivery gates

### D0 — Dossier readiness

Verify the dossier is source-linked, internally consistent, and covers acceptance, implementation, final gates, risks, and issue semantics. Bug fixes require evidence-backed current-state root cause, not causal Git provenance. `NOT_RUN` is a complete and acceptable provenance state when no trigger existed.

Do not fill gaps with plausible prose. Material contradictions return `NEEDS_CHANGES` or `NEEDS_ASSISTANCE` to the lead.

### D1 — Repository and authorization

Verify directly:

- git repository and intended authenticated remote;
- current branch/status/upstream, required base, and exact head;
- `base...head` changed files/patch and `base..head` branch commits;
- unrelated files/commits are absent or explicitly accounted for;
- no conflicting existing PR covers the same head/change when this remote check is needed for safe creation;
- every planned action fits `CREATE_PR`.

A dirty/unreviewed state, wrong base, stale canonical revision, conflicting PR, or unauthorized action blocks delivery.

### D2 — Branch manifest and included references

Compare the dossier with the actual base/head diff and branch commits. This is delivery validation, not causal provenance research.

Validate only references the dossier intends to include:

- same-repository commits resolve to the expected SHA/range;
- cross-repository commits use `owner/repo@sha` or a verified URL;
- PRs/issues resolve to the intended repository and relationship;
- causal references already carry their trigger, evidence, and confidence.

Do not search for extra related work just to make the PR look comprehensive. Do not invent issues, commits, tests, reviewers, co-authors, or causal links; rewrite published history; combine unrelated work; or create redundant commits.

### D3 — Reviewable composition

Coordinator generates the final title, body, and metadata with `creating-pr-content`; PR Delivery validates rather than rewrites it. Follow the repository template while preserving the compact semantic contract: problem or outcome, verified bug cause and introduction evidence when applicable, solution, exact observed verification, linked material commits/PRs/issues, and actual risks or limitations.

Default to 150–400 words excluding commands and links. Omit irrelevant sections instead of emitting `None`, raw logs, diff narration, or internal orchestration details. A changed head invalidates the content and its gate ledger.

Use `Closes #N` only when every issue acceptance criterion is implemented and verified. Otherwise use `Related to #N` or `Partially addresses #N` and name unmet criteria.

### D4 — Authorized create/update

1. create/validate only necessary authorized local commits;
2. push only the authorized branch;
3. create/update exactly the intended PR against the verified base;
4. capture URL, number, head, and state;
5. never merge.

Authentication, branch-protection, or provider blockers do not authorize alternate credentials or security changes.

### D5 — Remote read-back

Fetch the created/updated PR and verify:

- repository, URL/number, title, body, and draft state;
- base/head branches and exact head SHA;
- changed files and branch commits match the frozen manifest;
- included issue/PR/commit references resolve correctly;
- causal wording matches the dossier when provenance was included;
- checks/CI state is reported truthfully.

Remote existence is not correctness. Keep local gates, independent review, and external CI distinct; pending/failing CI is not green.

## Result format

```markdown
## Envelope status
PASS | NEEDS_CHANGES | NEEDS_ASSISTANCE | RETRYABLE | HARD_BLOCKED

## Delivery verdict
CREATED | UPDATED | BLOCKED

## Manifest
- repository / base / head / exact SHA:
- branch commits / changed files:

## Dossier represented
- acceptance / root cause / change:
- verification / findings / risk:
- provenance: NOT_RUN | CONFIRMED | LIKELY | UNKNOWN | N/A
- included references:
- omitted sections:

## PR
- URL/number/title/draft/issue linkage:

## Remote read-back
- fields/references/head verified:
- CI/check state:

## Risks or blockers
- ...
```

## Pitfalls

- Activating delivery before explicit `CREATE_PR` and final gate success.
- Building a dossier or making ordinary roles produce PR prose before activation.
- Treating a bug fix, issue, or PR as automatic permission for history analysis.
- Running blame/bisect or remote provenance searches to make a PR seem detailed.
- Letting PR Delivery fix production code or invent missing evidence.
- Closing unmet issue criteria or describing local checks as external CI.
- Treating `CREATE_PR` as permission to merge, deploy, label, comment, request reviewers, rewrite history, or change credentials.

## Completion

Delivery is complete only when the intended PR is created/updated within explicit authority and read back successfully; base/head/scope/branch manifest, included references, issue semantics, evidence, and CI state are truthful. Causal provenance may remain `NOT_RUN`; absence of unnecessary history work is not a gap.
