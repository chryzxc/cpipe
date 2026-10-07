# cpipe

[![CI](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml/badge.svg)](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-8957e5)](LICENSE)
[![Requires: Hermes Agent](https://img.shields.io/badge/requires-Hermes%20Agent-1f6feb)](https://github.com/NousResearch/hermes-agent)

cpipe is an **opt-in development workflow** for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
Start a delivery explicitly with `/cp-plan --size content|small|large`, or adopt committed work with
`/cp-review`, `/cp-fix`, `/cp-verify` or `/cp-ship`; ordinary coding requests stay in normal chat. Hermes profiles collaborate through a fixed graph and bounded feedback loops, producing a pull request
for human review. Content changes skip planning, test-writing and browser QA; small changes start tests and
implementation immediately; large changes require an approved plan first. Shipping runs required gates and
prepares a draft pull request by default. Marking it ready needs an explicit request; it never merges or deploys.

Each delivery is a single card on the Hermes Kanban board. Hermes profiles are the default runner: planning,
building, independent review and QA can collaborate in separate sessions, while the builder stays warm through tests, changes
and rework. Codex/Claude/opencode adapters remain opt-in; no external coding CLI is required for the Hermes
workflow. Deterministic code controls the process around it. Three principles shape the design:

- **[Graph engineering](#graph-engineering):** the workflow is a fixed graph defined in code. The agent never decides
  what happens next.
- **[Loop engineering](#loop-engineering):** every failure goes back to the same agent session, and each loop has a
  hard limit.
- **[Outcome learning](#outcome-learning):** verified gate recoveries inform later work in the same repository.
  Lessons are bounded hints, not automatic policy changes or proof that a gate passed.

> **Status: v2 · 0.9.0.** Explicit slash-command opt-in, content/small/large paths and profiled handoffs retain
> v2's deterministic orchestration. v1 ran each step in a new session and let the model choose transitions;
> the last v1 release is tagged `v1-final`. No speed or token savings are claimed without controlled measurement.

## Installation and quick start

Requires Python 3.11+ and a Hermes Agent installation with native plugin support and
session message injection. Install the plugin through the current plugin manager:

```sh
hermes plugins install chryzxc/cpipe --enable
hermes plugins doctor ~/.hermes/plugins/cpipe --ci
hermes plugins compat ~/.hermes/plugins/cpipe
hermes config set plugins.entries.cpipe.allow_gateway_injection true
```

The plugin declares `pyyaml>=6` and `agent-client-protocol>=0.9,<0.10`. Run installation
in an interactive terminal and review the dependency preparation prompt; `--enable`
does not grant dependency consent. To replace an existing installation, add `--force`.
For reproducible installation, add `--ref` with the full 40-character commit SHA of
the revision you intend to test. Use your active profile's plugin directory instead
of `~/.hermes/plugins/cpipe` when operating a separate profile.

Create or select the specialist profiles named in your configuration, then check
compatibility and start the worker daemon in a separate terminal:

```sh
hermes cpipe doctor
hermes cpipe run
```

Start a fresh chat with `/new` after reinstalling. Commands and hooks may hot-reload,
but model tools take effect in the next session. In the repository's project chat,
start with `/cp-plan --size small` followed by the requested change, or use `/cp-review`
to adopt an existing committed change. No delivery ID is required. Large plans wait
for approval; shipping prepares a draft PR and never merges or deploys.

The `gh` CLI and authorized GitHub access are needed for PR creation and remote-check
verification. Existing code-graph tools/indexes are optional; missing graph context
falls back to source inspection. Outcome learning requires no external service.

## Architecture

```
Explicit slash commands ── plan · build · review · fix · verify · ship · status · stop
      │
      ▼
Hermes Kanban: one card per delivery (the delivery's state)
      │ claimed by lane cp:<engine>
      ▼
cpipe daemon → supervisor → round
      │
setup ── large   → plan → human approval → tests ─┐
      ├─ small  → tests ────────────────────────┤
      └─ content ──────────────────────────────┤
                                               ▼
                 build ⇄ checks → verify → pull request → CI
                 builder session   QA · security · independent review
                                   (content skips browser QA)
      │
      ▼
review   the plan is ready for approval, or the change is ready to test
blocked  awaiting a decision
done     shipped; the pull request is ready for review
```

A **round** is one run of the graph. It runs from the card's current node until the next interrupt, and then hands
control back to the board. Feedback on a card starts a new round that resumes the same agent session: before
approval it revises the plan, after it it changes the code. Stage commands enter at the requested specialist gate
without restarting planning. Shipping checks the latest commit; it marks the PR ready only when explicitly requested.

**Plan (large/default route).** The agent studies the repository and writes a plan (what changes, the steps, how it will be tested), with
the questions only the developer can answer, each with a recommended answer. Nothing is built until the plan is
approved. When the plan has independent parts, each part becomes its own delivery with its own pull request;
parts that depend on others wait for them.

**Tests first (small/large).** Before any code changes, the agent writes tests and cpipe runs them: tests that pin existing
behavior must pass, and the new tests for the change must fail. Tests that already pass are sent back. The build
then makes them pass; if it changes a test written first, the reviewer is told to check the change.

**Review and refactor.** An independent reviewer reads the change against the request, including refactors that
clearly pay off in the changed code. Findings go back to the same session, then checks and verification run again.

## Graph engineering

The workflow is a directed graph defined in `cpipe/graph.py`. Nodes are steps and edges are outcomes. A single pure
function, `next_node(node, outcome, ready)`, determines every transition; `loop_outcome` applies the loop limits.

| Node type | Nodes | Characteristics |
|---|---|---|
| Agent | plan, tests, build, QA, review | Performed by the coding agent; consumes tokens |
| Code | setup, split, checks, security, pull request, CI, final | Deterministic; no model calls |

- **State on the card.** Each delivery is a single Kanban card, and its status is the only interrupt.
- **Verifiable.** Table tests cover every edge and limit. The complete graph also runs end to end against a
  scripted agent on generated repositories.

Code relationships are a separate graph, not a replacement for the workflow graph. For coding work,
cpipe queries an existing local `graphify-out/graph.json`, or falls back to an existing CodeGraph index.
The builder and independent reviewer receive bounded caller/dependency/test context. Queries are read-only,
local and cached by revision, dirty status, index timestamp and request; no additional model call is made.
Index freshness is not certified: specialists must confirm relationships against source and refresh the
local AST index after edits where supported. Missing tools, failed queries and timeouts fall back to source
inspection, never a graph-derived verification pass. Content-only work does not query code graphs.

## Outcome learning

Coding deliveries capture gate corrections for tests-first, checks, independent review, runtime QA,
security and remote CI. A local SQLite store at `$HERMES_HOME/state/cpipe-learning.sqlite` groups
observations by a hashed repository identity and delivery, so retries do not inflate counts and linked
worktrees share lessons without leaking them to unrelated repositories. Only predefined pattern keys and
counts are stored: no prompts, source, raw findings, credentials or private transcripts.

Failures remain observations until independent verification succeeds on the clean final revision; CI
recovery additionally requires green final-head remote CI. Up to five repository-scoped lessons are reused
in later builder prompts. Repeated verified recoveries expose a structural follow-up proposal (test, lint,
preflight check or canonical helper), not an automatic out-of-scope code/skill edit. Existing curated
project lessons remain supported. Store errors do not waive gates or fail an otherwise valid delivery.

This is bounded outcome-driven workflow learning, **not** model training or autonomous skill rewriting.
It does not infer the root cause from arbitrary failure text, mine unrelated chats, apply human corrections
as new policy, or claim a proposed structural follow-up has been implemented. Those need explicit scoping
and verification.

## Loop engineering

Each loop sends only the new information back to the existing agent session. When a loop reaches its limit, the
delivery stops and requests a decision.

| Loop | Input returned to the agent | Limit |
|---|---|---|
| tests first | Guard tests that fail, or new tests that already pass | 2 |
| build ⇄ checks | Output of the failing checks on the changed files (security checks share this budget) | 3 |
| build ⇄ QA | Failures observed in the running application | 2 |
| build ⇄ review | Findings from an independent reviewer; unresolved findings block at the limit | 2 |
| build ⇄ CI | The failing CI jobs and their logs | 2 |
| reviewer ⇄ delivery | Feedback from the reviewer | None |

The builder retains its session through tests, implementation and rework; planning, independent review and QA
use separate role sessions in a profiled workflow. Adapters resume their sessions (`codex exec resume`,
`claude --resume`, `hermes --resume`, `opencode --session`). Each round also has a wall-clock limit
(`round_minutes`); a delivery that reaches it pauses and continues from its last commit.

## Engines

Each engine runs as its own lane (`cp:codex`, `cp:claude`, and so on) with independent concurrency. The engine is
selected per delivery. A separate engine performs the review, and a fallback engine takes over when the primary
reaches a usage limit; it continues from the commits and `.cpipe/progress.md`.

| Engine (`kind`) | Session | Structured result |
|---|---|---|
| Codex CLI (`codex`) | `codex exec resume` | `--output-schema` |
| Claude Code (`claude`) | `claude -p --resume` | `--json-schema` |
| Hermes profile (`hermes`) | `hermes chat --resume` | JSON block in the final message |
| opencode (`opencode`) | `opencode run --session` | JSON block in the final message |

An engine's `kind` defaults to its name, so one CLI can back several engines (for example a Hermes profile per
engine).

## Configuration

`~/.hermes/cpipe.yaml` (all keys optional):

```yaml
command_only: true     # default: explicit slash commands; no model-callable delivery tools
default_engine: builder
fallback_engine: null   # optional, explicitly configured adapter for usage-limit recovery
roles: {discover: explorer, plan: planner, build: builder, review: reviewer, qa: verifier}
engines:
  explorer: {kind: hermes, profile: example-explorer, concurrency: 1} # optional
  planner:  {kind: hermes, profile: example-planner, concurrency: 1}
  builder:  {kind: hermes, profile: example-builder, concurrency: 3}
  reviewer: {kind: hermes, profile: example-reviewer, concurrency: 1}
  verifier: {kind: hermes, profile: example-verifier, concurrency: 1}
limits: {tests: 2, checks: 3, qa: 2, review: 2, ci: 2, round_minutes: 90}
env_pass: [GH_TOKEN]   # additional environment variables exposed to rounds and engines
projects_root: ~/Projects   # where the repository of a new project is started
```

The `example-*` profile names above are illustrative, not bundled profiles. Replace them with
the profile names actually installed on your machine. Hermes inherits each profile's configured model/provider;
`model` is an explicit override, never an inferred roster mapping. With no configuration, the default and review
engines are Hermes and no external fallback is enabled. `roles` is optional; explicitly configured engine choices
remain supported. Engine concurrency caps dispatcher delivery slots, not the QA/review calls inside each delivery.

### Optional discovery

Remove `roles.discover` to disable discovery (the default). When configured, large deliveries get a
bounded navigation pass before planning or a cold implementation session; small and content deliveries
skip it. Select a low-cost model/provider in your explorer profile: the runner inherits its configuration,
not the builder's model override. The `example-explorer` name is illustrative, not a bundled profile.

Discovery currently supports Hermes engines only. It supplies an existing Graphify/CodeGraph query
when available plus locally ranked source excerpts to a **tool-free**, isolated session. The explorer
cannot edit, run commands or recursively delegate. It returns a short source-linked brief covering
entry points, callers/dependencies, existing tests and unresolved questions. Partial snapshots and
graph hints are not plans, approvals or verification; the planner/builder must confirm important claims.
Ignored untracked files, symlinks and non-source files are excluded from the source snapshot.

The brief replaces duplicate graph context in the implementation prompt and is cached in the existing
git-excluded delivery state. Source content, revision, request, engine configuration or graph-context
changes invalidate it when discovery is next needed. The input snapshot and brief are bounded, and
the model call has a 60-second run budget with a 75-second subprocess cutoff. An unavailable,
failed or rate-limited explorer falls back to normal source discovery without a builder-model retry;
failed calls are not repeated for the same cache key. No extra card or approval step is created.
Discovery calls/time/tokens appear under `discover` in the existing usage accounting. Compare equivalent
end-to-end tasks with and without it before claiming cost, token or time savings.

### Profiled fast paths and handoffs

Select `size` explicitly with `/cp-plan --size content|small|large` (or with `cpipe_plan` in optional tool mode);
the runner does not guess task complexity:

- **`content`**: implement → deterministic checks and scope-limited review. No planning approval, test-writing turn
  or browser QA. Configured checks still run. Use for text, markup, styles, docs or config values, never logic.
- **`small`**: tests → implement → checks/QA/review. No planning turn or approval pause; the request authorizes
  the bounded scope.
- **`large`** (default when omitted): Planning → human plan approval → implementation → independent review/QA. Use for
  ambiguous, cross-boundary or risky work.

The planner and builder own separate sessions; the builder receives the approved scope, progress file and same worktree.
The builder stays warm through tests, implementation and fixes. Re-reviews use `last_reviewed_head..new_head` and verify
prior findings. A cold reviewer, changed profile or rewritten history gets the full branch range. QA never resumes
the builder's session. Legacy single-session checkpoints migrate without restarting their delivery. An unavailable
explicit reviewer/QA role cannot silently pass; project QA `off` and content-only QA remain intentional skips.
Review-budget exhaustion blocks for a decision; unresolved findings never become approval.

Reports include per-role agent calls/time, input/output/cache tokens and elapsed node time. Parallel QA/review
seconds are not added to elapsed wall time. These are measurement seams, **not measured speed/token savings**:
compare equivalent live tasks before making that claim. This preserves v2's fixed graph, not a wholesale v1 rollback.

A delivery's project is a name from `projects.yaml`. For a project cpipe does not know yet it asks where the
repository is (in chat through Hermes' clarify): the path to a git checkout, or `new` to start a repository under
`projects_root`. The answer is saved to `projects.yaml`, so later deliveries need only the name. A repository
without a remote keeps the delivery on a local branch: no push, no pull request.

Optional per-project settings live in `~/.hermes/delivery/`:

```yaml
# projects.yaml
projects:
  my-app:
    repo: ~/code/my-app                 # deliveries in worktrees of this repository use this profile
    base_branch: develop
    engine: claude                       # optional: this project's default engine
    conventions: "Run `npm test` before committing."   # first 10 lines are included in the build prompt
    qa: browser                          # browser | computer-use | off
    test_env: {services: {web: "npm run dev"}}

# quality.yaml: checks run in parallel against the changed files after each build
checks:
  - {name: lint, files: "*.ts *.vue", run: "npx eslint --max-warnings=0 {files}", projects: [my-app]}
  - {name: secrets, run: "gitleaks git --log-opts={base}..HEAD", stage: security}   # runs with QA and review
  # Checks are required by default. required: false permits an unavailable tool as explicit residual risk,
  # not as a pass. A real failure still requires correction.

# lessons.yaml: the 10 most recent entries are included in the build prompt ("*" applies to all projects)
my-app:
  - {text: "Run `prisma generate` before the tests"}
```

Repository conventions and agent skills belong with the engines, not with cpipe: an `AGENTS.md` in each
repository and the selected Hermes profile's skills. Optional external adapters use their own skill directories.

## Usage

```sh
hermes cpipe doctor   # verify Hermes compatibility, lanes and engine availability
hermes cpipe run      # start the daemon (a separate process, never inside the gateway)
hermes cpipe status   # open deliveries
hermes cpipe report   # duration, runs and tokens per delivery and node
```

Deliveries are **opt-in**, started by explicit slash commands. Ordinary coding requests stay on the normal
assistance path. `command_only: true` is the default: legacy unrestricted `cpipe_*` tools are disabled.
The receipt-gated `cpipe_execute_intent` bridge is registered so Hermes can resolve a command's target
from session context before executing its authorized backend action. Receipts expire, are session-bound
when the host supplies a session key, and prevent duplicate execution. They are not a sandbox against
shell/Python callers. Remove any old "always start cpipe" instructions from your coordinator.

## Session-aware chat commands

Type a command followed by a normal message. **No delivery ID is required.** Hermes uses the same
conversation history and native project context as reference, verifies the target, and uses its native
clarification tool only when necessary. The message is preserved verbatim rather than parsed as an ID.

```text
/cp-plan --size small fix the disabled button we discussed
/cp-build implement the plan above
/cp-review review the change I built outside cpipe
/cp-fix correct the review findings
/cp-verify verify the final behavior we discussed
/cp-ship let’s create a draft PR for this
/cp-status how are these changes doing?
/cp-stop stop the work we just started
```

An empty message uses the existing conversation. Project names and delivery IDs remain optional
references, not mandatory positional arguments. `--size content|small|large` guides `/cp-plan`;
unspecified delivery size remains `large` and requires plan approval.

- `/cp-plan` explicitly opts into a planned delivery; Hermes resolves the request and repository first.
- `/cp-build` builds the discussed plan: it resumes a matching delivery, or uses native tools for direct work.
- `/cp-review` adopts existing committed work or resumes its matching delivery for read-only specialist review.
  It reports one verdict and findings without editing, publishing or restarting implementation.
- `/cp-fix` sends bounded findings to the builder, then runs checks and independent verification of the new commit.
- `/cp-verify` runs configured checks, security checks, independent review and applicable QA. Bounded corrections
  stay with the builder. It stops with local evidence and does not publish.
- `/cp-ship` means complete the work, not just publish it. It works without a prior `/cp-plan`: it adopts
  existing work and independently reviews correctness, security, test adequacy and justified refactors in the
  changed code. The builder adds missing regression/characterization tests, fixes problems and refactors only
  when warranted, then runs focused tests and the applicable existing regression suite. Corrections receive
  independent re-review and final-revision verification, including applicable QA and configured security checks.
  Existing correct behavior may already pass new characterization tests; adopted work never earns retrospective
  TDD claims. Content-only work does not require unrelated tests or refactors. Missing evidence is a finding,
  not approval; unresolved findings and unavailable required gates block shipping. Unrelated rewrites remain
  outside scope. Valid complete-review receipts are reused rather than calling every bot again.
  Only after these gates does it create/update a **draft PR** by default. Marking a PR ready must be requested explicitly and requires the current revision's
  review, verification and CI. Shipping **never merges**.
- `/cp-status` reads real Git/PR or matching delivery evidence; it does not start or mutate work.
- `/cp-stop` targets only the identified work, preserving files and unrelated deliveries/services.

Commands queue a native Hermes user turn; the initial reply reports **queued**, not completed.
The host must support `inject_message`. Desktop/TUI also requires the plugin permission:

```sh
hermes config set plugins.entries.cpipe.allow_gateway_injection true
```

If the host cannot queue into the current session, the command reports that no work started. It does
not fall back to an ID parser or choose the newest unrelated delivery. No workflow starts if queuing fails.

For backend deliveries, internal tests/build/QA/review handoffs still run automatically after the
explicit start (and approval for large work). Hermes' native clarification answers can revise/resume
only the existing waiting delivery they belong to; unrelated chat cannot start a new delivery.
There is no `/cp-feedback` or `/cp-project`: use native clarification and Hermes project context.

### Adopt work without rebuilding it

Late entry requires a clean, committed source checkout, a resolved PR base and the intended scope from the
conversation. cpipe snapshots that exact commit onto a separate delivery branch; it does not stash, reset or
switch the source checkout. It records source/base provenance and explicitly does **not** claim retrospective TDD.
The daemon and configured specialist profiles must be available, just as for a planned delivery.

A typical path is `/cp-review` → `/cp-fix` (if needed) → `/cp-verify` → `/cp-ship`. You can enter directly at
verification or shipping; those commands still enforce the required gates. Source changes committed after adoption
are not silently copied into the snapshot: adopt their new revision explicitly.

Receipts bind approvals to the exact commit, diff base, scope and gate configuration. Unchanged gates are reused
without another model call; changed inputs invalidate them. The builder resumes its own session, and the reviewer
uses the delta plus prior finding IDs after corrections. QA, security and review run concurrently when applicable.
Required checks that cannot run, missing required QA and pending/cancelled/skipped CI block rather than pass.
An absent check configuration is reported as such, never described as a test pass.

Set `command_only: false` in `cpipe.yaml` only to deliberately expose the optional legacy backend tools:
`cpipe_plan`, `cpipe_build`, `cpipe_ship`, `cpipe_status`, and `cpipe_stop`. This is compatibility mode,
not the recommended session-aware command interface.

The chat that starts a delivery is notified when the plan is ready, when the pull request is ready to test, when
a decision is needed, and when the delivery completes. GitHub access uses the `gh` CLI; without it, the branch is pushed and no pull request
is opened.

## Project layout

```
cpipe/graph.py           workflow graph, transition function and loop limits
cpipe/code_context.py    bounded read-only code-relationship context
cpipe/learning.py        repository-scoped verified gate outcomes
cpipe/intents.py         explicit-command receipts and session-aware authorization
cpipe/native.py          native message injection and clarification hooks
cpipe/round.py           round driver (python -m cpipe.round <task>): every node
cpipe/engines/           engine integrations: Codex, Claude Code, Hermes profile, opencode
cpipe/pr.py              push, pull request, CI monitoring, public-text scan
cpipe/tools.py           session-aware commands, stage adoption and backend actions
cpipe/report.py          status and report
cpipe/settings.py        cpipe.yaml loader; one runtime lane per engine
cpipe/quality.py         checks and security nodes (quality.yaml)
cpipe/workspace_prep.py  setup node: worktree refresh, dependency and code-index links
cpipe/test_env.py        application runner for QA (projects.yaml test_env)
cpipe/runtime/           bundled worker runtime: scheduler, supervisor, lifecycle, adapters
docs/runtime/            runtime documentation
```

## Roadmap

- [x] Bundled worker runtime, lane configuration, generated test repositories
- [x] Codex engine; setup → build ⇄ checks; feedback rounds resume the same session
- [x] Push, pull request, CI monitoring, security checks, scan of public text
- [x] Independent review by a second engine; final checks; Hermes chat tools
- [x] Claude Code, Hermes profile and opencode engines; usage-limit fallback; browser and computer-use QA
- [x] Parallel deliveries; decomposition of large tasks into child deliveries
- [x] Reporting: duration, token usage and rounds per node
- [x] Plan approval with questions; tests first, checked by running them; review with refactoring
- [x] Explicit slash-command opt-in; content/small/large workflow selection
- [x] Hermes role handoffs; persistent builder sessions and delta-scoped re-review
- [x] Per-role timing and token reporting without conflating parallel time with wall-clock time
- [x] Late-entry review/fix/verification, frozen-revision gate reuse and draft-by-default shipping
- [x] Bounded local code-graph context for builders and independent reviewers
- [x] Repository-scoped gate-outcome learning with final-head CI evidence for CI recovery

## Safety

cpipe never merges, deploys or handles credentials. Engines work only inside the delivery's own git worktree.
Pushing and opening pull requests are done by cpipe's own code, not by the agent. Before anything is pushed, commit messages and added lines
are scanned for local paths and internal ids, and the pull request text is redacted. Computer-use QA runs one
delivery at a time.

## Contributing

The test suite runs against repositories generated from templates (`tests/fixtures/make_repo.py`, Node and Python)
and a temporary Hermes board. It never touches the local `~/.hermes`. Engines and GitHub are replaced with scripted
fakes (`tests/fake_codex.py`, `tests/fake_gh.py`).

```sh
uv venv --python <hermes-agent venv python> .venv
uv pip install --python .venv/bin/python pytest pyyaml "agent-client-protocol>=0.9,<0.10"
# The full suite requires a Hermes Agent checkout on sys.path (e.g. via a .pth file).
.venv/bin/python -m pytest -q
```

The complete suite includes tests that import `hermes_cli` during collection. A
standalone environment without the Hermes Agent checkout cannot run that full
suite; some older modules skip, but not all integration modules do. Local plugin
validation and the hosted CI badge are separate evidence: do not describe remote
CI as passing merely because local tests or Plugin Doctor passed.

## License

[MIT](LICENSE) © 2026 Christian Rey Villablanca
