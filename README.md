# cpipe

[![CI](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml/badge.svg)](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-8957e5)](LICENSE)
[![Requires: Hermes Agent](https://img.shields.io/badge/requires-Hermes%20Agent-1f6feb)](https://github.com/NousResearch/hermes-agent)

cpipe is a [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that delivers software changes as
pull requests that are tested, quality-assured and reviewed before they reach a human.

Each delivery is a single card on the Hermes Kanban board. A coding agent (Codex, Claude Code, a Hermes profile or
opencode) carries out the work in one continuous session. Deterministic code controls the process around it. Two
principles shape the design:

- **[Graph engineering](#graph-engineering):** the workflow is a fixed graph defined in code. The agent never decides
  what happens next.
- **[Loop engineering](#loop-engineering):** every failure goes back to the same agent session, and each loop has a
  hard limit.

> **Status: v2.** v2 replaces the v1 multi-agent workflow, which ran each step in a new session and let the model
> choose transitions; that made it slow and costly. The last v1 release is tagged `v1-final`.

## Architecture

```
Hermes chat ── ship · feedback · ready · status · stop
      │
      ▼
Hermes Kanban: one card per delivery (the delivery's state)
      │ claimed by lane cp:<engine>
      ▼
cpipe daemon → supervisor → round
      │
setup → build ⇄ checks → verify ─────────────────────── → pull request → CI
code    agent    code     QA · security · review (parallel)     code          code
                          agent  code     2nd agent
      │
      ▼
review   ready for acceptance testing
blocked  awaiting a decision
done     accepted; the pull request is ready to merge
```

A **round** is one run of the graph. It runs from the card's current node until the next interrupt, and then hands
control back to the board. Feedback on a card starts a new round that resumes the same agent session. Accepting a
delivery runs the final checks on the latest commit and marks the pull request ready to merge.

Large tasks are planned first in the same session. When the plan has independent parts, each part becomes its own
delivery with its own pull request; parts that depend on others wait for them.

## Graph engineering

The workflow is a directed graph defined in `cpipe/graph.py`. Nodes are steps and edges are outcomes. A single pure
function, `next_node(node, outcome, ready)`, determines every transition; `loop_outcome` applies the loop limits.

| Node type | Nodes | Characteristics |
|---|---|---|
| Agent | plan, build, QA, review | Performed by the coding agent; consumes tokens |
| Code | setup, split, checks, security, pull request, CI, final | Deterministic; no model calls |

- **State on the card.** Each delivery is a single Kanban card, and its status is the only interrupt.
- **Verifiable.** Table tests cover every edge and limit. The complete graph also runs end to end against a
  scripted agent on generated repositories.

## Loop engineering

Each loop sends only the new information back to the existing agent session. When a loop reaches its limit, the
delivery stops and requests a decision.

| Loop | Input returned to the agent | Limit |
|---|---|---|
| build ⇄ checks | Output of the failing checks on the changed files (security checks share this budget) | 3 |
| build ⇄ QA | Failures observed in the running application | 2 |
| build ⇄ review | Findings from an independent reviewer; findings still open at the limit are listed in the pull request | 2 |
| build ⇄ CI | The failing CI jobs and their logs | 2 |
| reviewer ⇄ delivery | Feedback from the reviewer | None |

The agent keeps its context for the whole delivery (`codex exec resume`, `claude --resume`, `hermes --resume`,
`opencode --session`). Repeated rounds stay small and fast. Each round also has a wall-clock limit
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
default_engine: codex
fallback_engine: claude
review_engine: claude
engines:
  codex:  {bin: codex, model: <model>, reasoning: medium, plan_reasoning: high, qa_profile: <codex profile>,
           concurrency: 3}
  claude: {bin: claude, model: <model>, concurrency: 2}
  coder:  {kind: hermes, profile: <hermes profile>, concurrency: 2}
  opencode: {model: <provider/model>, concurrency: 1}
limits: {checks: 3, qa: 2, review: 2, ci: 2, round_minutes: 90}
env_pass: [GH_TOKEN]   # additional environment variables exposed to rounds and engines
projects_root: ~/Projects   # where the repository of a new project is started
```

A delivery's project is a name from `projects.yaml`, a path to a repository, or a new name. A new name starts a
new repository under `projects_root` (shipping to the same name again reuses it). A repository without a remote
keeps the delivery on a local branch: no push, no pull request.

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
repository, and skills under `~/.codex/skills` or `~/.claude/skills`.

## Usage

```sh
hermes cpipe doctor   # verify Hermes compatibility, lanes and engine availability
hermes cpipe run      # start the daemon (a separate process, never inside the gateway)
hermes cpipe status   # open deliveries
hermes cpipe report   # duration, runs and tokens per delivery and node
```

Deliveries are started and steered from Hermes chat through the `cpipe` toolset:

| Tool | Effect |
|---|---|
| `cpipe_ship` | Starts a delivery: one card with its own worktree and branch. The engine and model can be named in the request |
| `cpipe_feedback` | Sends requested changes; the same session takes another round and updates the pull request |
| `cpipe_ready` | Accepts the delivery: final checks, then the pull request is marked ready to merge |
| `cpipe_status` | Reports where one delivery, or every open delivery, stands |
| `cpipe_stop` | Stops a delivery and archives its card |

The same actions are slash commands, typed directly into a Hermes chat. They answer at once, without a model turn:

```text
/cp-ship [project:] <request>      /cp-feedback <card> <changes>      /cp-ready <card> [last changes]
/cp-status [card]                  /cp-stop <card>
```

For example `/cp-ship my-app: add a dark mode toggle`, `/cp-ship ~/code/tool: fix the CLI help`, or
`/cp-ship recipe-site: a static site that lists recipes` for a project that has no repository yet.

The chat that starts a delivery is notified when the pull request is ready to test, when a decision is needed, and
when the delivery completes. GitHub access uses the `gh` CLI; without it, the branch is pushed and no pull request
is opened.

## Project layout

```
cpipe/graph.py           workflow graph, transition function and loop limits
cpipe/round.py           round driver (python -m cpipe.round <task>): every node
cpipe/engines/           engine integrations: Codex, Claude Code, Hermes profile, opencode
cpipe/pr.py              push, pull request, CI monitoring, public-text scan
cpipe/tools.py           Hermes chat tools
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

## Safety

cpipe never merges, deploys or handles credentials. Engines work only inside the delivery's own git worktree. Pushing and opening pull
requests are done by cpipe's own code, not by the agent. Before anything is pushed, commit messages and added lines
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
