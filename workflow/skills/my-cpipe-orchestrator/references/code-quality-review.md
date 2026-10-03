# Code Quality Review

Load for every meaningful `Reviewer / code` assignment. This rubric supplements `independent-code-review`; it does not replace correctness, compatibility, security, or repository-specific gates.

## Standard precedence

Apply standards in this order:

1. repository instructions and documented architecture;
2. enforced formatter, linter, compiler, type checker, tests, and build rules;
3. established analogous code and local conventions;
4. language/framework official conventions supported by installed skills or authoritative documentation;
5. general principles such as cohesion, separation of concerns, SOLID, DRY, YAGNI, and composition over inheritance.

General principles are heuristics, not automatic findings. Every finding needs evidence and concrete impact.

## Scope and exact state

Review the frozen diff plus relevant callers, tests, contracts, generated boundaries, new/untracked files, and deletions. Classify baseline debt separately. Do not expand a narrow task into unrelated cleanup.

## Review dimensions

### 1. Specification and behavior

- acceptance and non-goals are satisfied;
- output, state transitions, negative/edge/failure paths are correct;
- root cause is fixed rather than wrapped with a symptom flag;
- public/internal contracts and callers remain compatible;
- authorization, validation, persistence, and errors compose correctly.

### 2. Module and file design

- each changed module has a coherent responsibility and authoritative owner;
- business rules do not leak across UI/transport/domain/persistence boundaries against repository architecture;
- dependency direction is understandable and cycles/action-at-a-distance are avoided;
- extraction improves ownership, navigation, testing, or change isolation rather than merely reducing line count.

#### Handwritten file-size policy

Unless a project supplies a stricter policy, use these defaults:

| Physical lines | Treatment |
| --- | --- |
| Up to 500 | No size finding by itself |
| 501–1,000 | Mandatory decomposition review; find only with evidenced mixed responsibility, navigation, testing, or change-amplification cost |
| Above 1,000 | Strong presumption against new or substantially expanded handwritten code; normally Important unless a concrete cohesive exception is justified |

Rules:

- line count triggers inspection; it is not proof of bad design;
- existing oversized files are baseline debt and do not automatically block a narrow change;
- flag an existing large file only when the change materially worsens responsibility mixing or misses an obvious bounded extraction;
- exclude generated/vendored code, lockfiles, snapshots, fixtures/test data, compiled artifacts, machine-generated schemas, and repository-declared equivalents;
- report counted files, exclusions, and justifications;
- never split a cohesive file into arbitrary fragments solely to satisfy a threshold.

### 3. Function, method, and class design

- each function performs a coherent operation with explicit inputs, outputs, and side effects;
- parameters and return values are bounded and comprehensible;
- dependencies are explicit where isolation/testing benefits;
- classes represent durable responsibility/state rather than namespaces for unrelated helpers;
- inheritance represents substitutability; prefer composition when clearer;
- function length/parameter count are inspection triggers unless repository tools impose hard limits.

### 4. Control flow and state modeling

- avoid needless nesting, hidden mutation, swallowed errors, and duplicated policy branches;
- ordering, async/concurrent behavior, retry, idempotency, and partial failure are explicit;
- state transitions are valid, observable, and owned once;
- closed domains use stack-native typed representations when that improves validity and compatibility.

#### Enums and closed domains

Prefer an enum, typed union, discriminated union, sealed type, constant object, schema-owned type, or equivalent when values form a stable closed set, recur across branches/modules, should reject invalid values, drive transitions, or have defined persistence/API mappings.

Do not force an enum for open-ended, user/provider/configuration-defined values, one-use literals, generated-schema ownership, or languages where enum serialization/interoperability is worse than another typed representation.

### 5. Magic strings and numbers

A literal is magic when its meaning, unit, ownership, or change policy is unclear—especially when repeated, coupled across layers, or embedded in business rules, transitions, permissions, protocols, persistence, or public contracts.

Inspect timeouts, retries, thresholds, percentages, page sizes, ports, status/error codes, feature/event/route/storage keys, selectors, and numeric state codes.

Use the narrowest appropriate owner:

1. local named constant for local meaning;
2. module/domain constant for a shared invariant;
3. enum/union/sealed type for a stable closed set;
4. validated configuration for legitimate operator/deployment control;
5. domain value object/type for units, validation, serialization, or behavior;
6. authoritative schema/generated contract when another system owns values.

Do not demand constants for self-evident literals such as an empty collection, a one-use loop index, `0` initial accumulator, or `1` increment when no domain meaning/coupling exists. Tests may use direct expected literals when clearer, but repeated contract values should share the authoritative type/constant where drift is possible.

A magic-value finding must demonstrate ambiguity, duplication, unsafe coupling, search/update difficulty, or maintenance risk. “It is a literal” is insufficient.

### 6. Abstraction, cohesion, and coupling

A new abstraction must eliminate proven duplication, enforce an invariant/boundary, isolate volatile I/O, or materially improve testing. Flag pass-through wrappers, speculative factories/interfaces, one-use generic utilities, configuration sprawl, mirrored state, and avoidable layer chains.

Do not flatten legitimate trust, transaction, compatibility, domain, or platform boundaries.

### 7. DRY, reuse, and YAGNI

Search for canonical helpers before adding new ones. Consolidate duplicated business rules, but permit small local duplication when a shared abstraction would couple unrelated concepts. Reject unrequested extension points, generalized frameworks, options, and future-proofing without current acceptance evidence.

### 8. Naming, comments, and readability

- names expose domain meaning, units, and side effects;
- booleans/predicates read clearly;
- constants reveal ownership of non-obvious values;
- comments explain why, constraints, or risk—not restate code;
- dead, commented-out, debug, unreachable, and misleading code is absent;
- input → validation → decision → side effect → result is traceable.

### 9. Error handling and observability

- no swallowed or over-broadly translated failures;
- status/error mappings remain meaningful and compatible;
- retries are bounded and idempotent where needed;
- compensation/reconciliation is durable for partial external effects;
- logs are actionable and avoid secrets/unnecessary PII;
- failure states are testable and observable.

### 10. Test quality

- tests cover observable behavior and the original regression;
- happy, negative, edge, and failure cases match risk;
- names state behavior;
- mocks model real contracts instead of weakening production boundaries;
- nondeterminism, hidden environment dependence, choreography-only assertions, and excessive mocking are challenged;
- behavior-preserving refactors have characterization/regression evidence.

### 11. Performance and security triage

`Reviewer / code` performs smell-level triage only: unbounded loops, N+1 queries, repeated serialization, render churn, bundle growth, untrusted input, authorization, sensitive logging, and unsafe process/file/query/template behavior. Route material concerns to `Verifier / performance` or `Security Reviewer` for supported verdicts.

## Framework/language skill discovery

The reviewer may load additional installed stack-specific skills when each materially improves the bounded review. Examples include TypeScript, React, Next.js, Python, Django, database, testing, accessibility, or security skills—but only when the exact skill exists and readiness is verified.

Do not infer a skill from the technology name. If no exact skill exists, use repository rules, compiler/type/lint/test output, analogous code, live tool help, and official documentation. Report actual skills/resources and substitutions. Additional skills improve technique; they never expand files, permissions, external actions, product decisions, delegation, or user contact.

## Finding taxonomy

- `REV-N`: correctness, contract, compatibility, authorization, scope, or error-path defect. Severity may be Critical, High, Important, Medium, Low, or Suggestion.
- `MAINT-N`: modularity, cohesion, coupling, naming, duplication, testability, file-size decomposition, abstraction, magic value, or change-amplification defect. `maintainability-refactor.md` exclusively owns its Important/Medium/Low/Suggestion severity and remediation rules.
- `SEC-N` and `PERF-N`: produced by their dedicated gates, not invented by Code Review.

Every actionable finding includes:

```text
ID: REV-N | MAINT-N
severity: use the family-specific policy above
confidence: High | Medium | Low
evidence: exact path:line plus callers/tests/analogues
principle: repository rule or concrete review dimension
problem: exact defect
impact: correctness, compatibility, debugging, testing, or future-change cost
direction: smallest concrete remediation
risk: SAFE | CAREFUL | RISKY
scope: expected files/boundary
verification: focused and broad checks
```

Invalid findings include “not best practice,” file length without maintenance evidence, taste contradicted by local convention, unconditional demands for enums/constants/interfaces/factories, extracting every literal, speculative extensibility, unrelated legacy cleanup, or rewrite preference without a smaller repair direction.

## Review-cycle contract

- **Pass-one exhaustiveness:** the first `REQUEST_CHANGES` on a frozen range enumerates every observed finding in one verdict, numbered `REV-001…N` / `MAINT-001…N`, severity-tagged, with file/line evidence each. A later verdict on the same card may not introduce a finding that was observable in an earlier reviewed range without stating why it was missed in pass one.
- **Delta-scope rework review:** a rework review covers only (a) the delta `last_reviewed_head..new_head` and (b) verification that every prior finding ID is resolved or explicitly contested with evidence. The base/head-movement gate still reverts to full-range review when the movement exceeds the rework delta.
- **Cycle escalation:** after 3 `changes_requested` cycles on one card, stop iterating and reconcile scope instead — batch remaining findings, split the card, park an unsatisfiable finding as its own blocked card, or accept the risk explicitly.

## Pass and recovery

Approve only when no blocking `REV-N`, Important in-scope `MAINT-N`, unresolved required specialist referral, or unjustified new/substantially expanded handwritten file above the configured strong threshold remains; required repository gates pass or gaps are routed; and the verdict covers the current frozen state.

The reviewer stays read-only. The lead validates/deduplicates findings, routes correctness edits to `Implementer / finding-fix`, and applies `maintainability-refactor.md` to every `MAINT-N`. Every affected edit requires fresh gates and review.

## Result additions

Report:

- exact frozen state and scope;
- project rules/tools applied;
- changed-file size classification, exclusions, and justifications;
- `REV-N` and `MAINT-N` tables;
- security/performance referrals;
- commands and observed results;
- unavailable/unreviewed paths;
- `APPROVED`, `REQUEST_CHANGES`, or `BLOCKED`, mapped to the canonical envelope.
