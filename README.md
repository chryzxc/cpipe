# cpipe

[![CI](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml/badge.svg)](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-8957e5)](LICENSE)
[![Requires: Hermes Agent](https://img.shields.io/badge/requires-Hermes%20Agent-1f6feb)](https://github.com/NousResearch/hermes-agent)

cpipe is an **opt-in development workflow** for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
Start a delivery explicitly with `/cp-plan --size content|small|large`; ordinary coding requests stay in normal
chat. Hermes profiles collaborate through a fixed graph and bounded feedback loops, producing a pull request
for human review. Content changes skip planning, test-writing and browser QA; small changes start tests and
implementation immediately; large changes require an approved plan first. Shipping runs final checks and marks
the pull request ready for review—it never merges or deploys.

Each delivery is a single card on the Hermes Kanban board. Hermes profiles are the default runner: Planner,
Forge, Sentry and QA can collaborate in separate sessions, while the builder stays warm through tests, changes
and rework. Codex/Claude/opencode adapters remain opt-in; no external coding CLI is required for the Hermes
workflow. Deterministic code controls the process around it. Two principles shape the design:

- **[Graph engineering](#graph-engineering):** the workflow is a fixed graph defined in code. The agent never decides
  what happens next.
- **[Loop engineering](#loop-engineering):** every failure goes back to the same agent session, and each loop has a
  hard limit.

> **Status: v2 · 0.8.0.** Explicit slash-command opt-in, content/small/large paths and profiled handoffs retain
> v2's deterministic orchestration. v1 ran each step in a new session and let the model choose transitions;
> the last v1 release is tagged `v1-final`. No speed or token savings are claimed without controlled measurement.

## Architecture

```
Explicit slash commands ── plan · feedback · build · ship · status · stop
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
approval it revises the plan, after it it changes the code. Shipping runs the final checks on the latest commit and
marks the pull request ready for review.

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

## Loop engineering

Each loop sends only the new information back to the existing agent session. When a loop reaches its limit, the
delivery stops and requests a decision.

| Loop | Input returned to the agent | Limit |
|---|---|---|
| tests first | Guard tests that fail, or new tests that already pass | 2 |
| build ⇄ checks | Output of the failing checks on the changed files (security checks share this budget) | 3 |
| build ⇄ QA | Failures observed in the running application | 2 |
| build ⇄ review | Findings from an independent reviewer; findings still open at the limit are listed in the pull request | 2 |
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
roles: {plan: planner, build: builder, review: reviewer, qa: verifier}
engines:
  planner:  {kind: hermes, profile: archon, concurrency: 1}
  builder:  {kind: hermes, profile: forge, concurrency: 3}
  reviewer: {kind: hermes, profile: sentry, concurrency: 1}
  verifier: {kind: hermes, profile: sentinel, concurrency: 1}
limits: {tests: 2, checks: 3, qa: 2, review: 2, ci: 2, round_minutes: 90}
env_pass: [GH_TOKEN]   # additional environment variables exposed to rounds and engines
projects_root: ~/Projects   # where the repository of a new project is started
```

Use the profile names actually installed on your machine. Hermes inherits each profile's configured model/provider;
`model` is an explicit override, never an inferred roster mapping. With no configuration, the default and review
engines are Hermes and no external fallback is enabled. `roles` is optional; explicitly configured engine choices
remain supported. Engine concurrency caps dispatcher delivery slots, not the QA/review calls inside each delivery.

### Profiled fast paths and handoffs

Select `size` explicitly with `/cp-plan --size content|small|large` (or with `cpipe_plan` in optional tool mode);
the runner does not guess task complexity:

- **`content`**: implement → deterministic checks and scope-limited review. No planning approval, test-writing turn
  or browser QA. Configured checks still run. Use for text, markup, styles, docs or config values, never logic.
- **`small`**: tests → implement → checks/QA/review. No planning turn or approval pause; the request authorizes
  the bounded scope.
- **`large`** (default when omitted): Planner → human plan approval → Forge → independent review/QA. Use for
  ambiguous, cross-boundary or risky work.

Planner and Forge own separate sessions; Forge receives the approved scope, progress file and same worktree.
Forge stays warm through tests, implementation and fixes. Re-reviews use `last_reviewed_head..new_head` and verify
prior findings. A cold reviewer, changed profile or rewritten history gets the full branch range. QA never resumes
the builder's session. Legacy single-session checkpoints migrate without restarting their delivery. An unavailable
explicit reviewer/QA role cannot silently pass; project QA `off` and content-only QA remain intentional skips.
Review-budget exhaustion still carries open findings into the PR rather than claiming they were resolved.

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
/cp-ship let’s create a draft PR for this
/cp-status how are these changes doing?
/cp-stop stop the work we just started
```

An empty message uses the existing conversation. Project names and delivery IDs remain optional
references, not mandatory positional arguments. `--size content|small|large` guides `/cp-plan`;
unspecified delivery size remains `large` and requires plan approval.

- `/cp-plan` explicitly opts into a planned delivery; Hermes resolves the request and repository first.
- `/cp-build` builds the discussed plan: it resumes a matching delivery, or uses native tools for direct work.
- `/cp-ship` also supports changes made directly earlier in the session, without a prior `/cp-plan`.
  A draft request creates/updates a **draft PR**, not a ready PR. Unspecified PR state defaults to draft.
  Existing delivery acceptance can use its final-check/readiness transition. Shipping **never merges**.
- `/cp-status` reads real Git/PR or matching delivery evidence; it does not start or mutate work.
- `/cp-stop` targets only the identified work, preserving files and unrelated deliveries/services.

Commands queue a native Hermes user turn; the initial reply reports **queued**, not completed.
The host must support `inject_message`. Desktop/TUI also requires the plugin permission:

```sh
hermes config set plugins.entries.cpipe.allow_gateway_injection true
```

If the host cannot queue into the current session, the command reports that no work started. It does
not fall back to an ID parser, choose the newest delivery, or create a delivery to publish direct changes.

For backend deliveries, internal tests/build/QA/review handoffs still run automatically after the
explicit start (and approval for large work). Hermes' native clarification answers can revise/resume
only the existing waiting delivery they belong to; unrelated chat cannot start a new delivery.
There is no `/cp-feedback` or `/cp-project`: use native clarification and Hermes project context.

Set `command_only: false` in `cpipe.yaml` only to deliberately expose the optional legacy backend tools:
`cpipe_plan`, `cpipe_build`, `cpipe_ship`, `cpipe_status`, and `cpipe_stop`. This is compatibility mode,
not the recommended session-aware command interface.

The chat that starts a delivery is notified when the plan is ready, when the pull request is ready to test, when
a decision is needed, and when the delivery completes. GitHub access uses the `gh` CLI; without it, the branch is pushed and no pull request
is opened.

## Project layout

```
cpipe/graph.py           workflow graph, transition function and loop limits
cpipe/round.py           round driver (python -m cpipe.round <task>): every node
cpipe/engines/           engine integrations: Codex, Claude Code, Hermes profile, opencode
cpipe/pr.py              push, pull request, CI monitoring, public-text scan
cpipe/tools.py           Hermes chat tools and slash commands: plan, feedback, build, ship
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
# Board tests require the hermes-agent checkout on sys.path (e.g. via a .pth file); otherwise they are skipped.
.venv/bin/python -m pytest -q
```

## License

[MIT](LICENSE) © 2026 Christian Rey Villablanca
