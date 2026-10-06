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

> **Status: v2, under active development.** v2 replaces the v1 multi-agent workflow, which ran each step in a new
> session and let the model choose transitions; that made it slow and costly. The last v1 release is tagged
> `v1-final`. See the [roadmap](#roadmap) for what is implemented today.

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
setup → build ⇄ checks → QA → security → review → pull request → CI
code    agent    code     agent   code   2nd agent     code        code
      │
      ▼
review   ready for acceptance testing
blocked  awaiting a decision
done     accepted; the pull request is ready to merge
```

A **round** is one run of the graph. It runs from the card's current node until the next interrupt, and then hands
control back to the board. Feedback on a card starts a new round that resumes the same agent session.

## Graph engineering

The workflow is a directed graph defined in `cpipe/graph.py`. Nodes are steps and edges are outcomes. A single pure
function, `next_node(node, outcome, rounds, limits)`, determines every transition.

| Node type | Nodes | Characteristics |
|---|---|---|
| Agent | build, QA, review | Performed by the coding agent; consumes tokens |
| Code | setup, checks, security, pull request, CI | Deterministic; no model calls |

- **State on the card.** Each delivery is a single Kanban card, and its status is the only interrupt.
- **Verifiable.** Table tests cover every edge and limit. The complete graph also runs end to end against a
  scripted agent on generated repositories.

## Loop engineering

Each loop sends only the new information back to the existing agent session. When a loop reaches its limit, the
delivery stops and requests a decision.

| Loop | Input returned to the agent | Limit |
|---|---|---|
| build ⇄ checks | Output of the failing checks on the changed files | 3 |
| build ⇄ QA | Failures observed in the running application | 2 |
| build ⇄ review | Findings from an independent reviewer | 2 |
| build ⇄ CI | The failing CI jobs | 2 |
| reviewer ⇄ delivery | Comments on the card | None |

The agent keeps its context for the whole delivery (`codex exec resume`, `claude --resume`). Repeated rounds stay
small and fast.

## Engines

Each engine runs as its own lane (`cp:codex`, `cp:claude`, and so on) with independent concurrency. The engine is
selected per delivery. A separate engine performs the review, and a fallback engine takes over when the primary
reaches a usage limit.

| Engine | Status |
|---|---|
| Codex CLI | Available |
| Claude Code | Planned |
| Hermes profile | Planned |
| opencode (ACP) | Planned |

## Configuration

`~/.hermes/cpipe.yaml` (all keys optional):

```yaml
default_engine: codex
fallback_engine: claude
review_engine: claude
engines:
  codex:  {bin: codex, model: <model>, reasoning: medium, concurrency: 3}
  claude: {bin: claude, model: <model>, concurrency: 2}
limits: {checks: 3, qa: 2, review: 2, ci: 2, round_minutes: 90}
env_pass: [GH_TOKEN]   # additional environment variables exposed to rounds and engines
```

Per-project settings live in `~/.hermes/delivery/`:

```yaml
# projects.yaml
projects:
  my-app:
    repo: ~/code/my-app                 # deliveries in worktrees of this repository use this profile
    base_branch: develop
    conventions: "Run `npm test` before committing."   # first 10 lines are included in the build prompt
    qa: browser                          # browser | computer-use | off
    test_env: {services: {web: "npm run dev"}}

# quality.yaml: checks run against the changed files after each build
checks:
  - {name: lint, files: "*.ts *.vue", run: "npx eslint --max-warnings=0 {files}", projects: [my-app]}

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
```

## Project layout

```
cpipe/graph.py           workflow graph and transition function
cpipe/round.py           round driver (python -m cpipe.round <task>)
cpipe/engines/           engine integrations (Codex)
cpipe/settings.py        cpipe.yaml loader; one runtime lane per engine
cpipe/quality.py         checks node (quality.yaml)
cpipe/workspace_prep.py  setup node: worktree refresh, dependency and code-index links
cpipe/test_env.py        application runner for QA (projects.yaml test_env)
cpipe/flow.py            per-delivery Hermes conversation
cpipe/runtime/           bundled worker runtime: scheduler, supervisor, lifecycle, adapters
docs/runtime/            runtime documentation
```

## Roadmap

- [x] Bundled worker runtime, lane configuration, generated test repositories
- [x] Codex engine; setup → build ⇄ checks; feedback rounds resume the same session
- [ ] Push, pull request, CI monitoring, security checks, scan of public text
- [ ] Independent review by a second engine; final checks; Hermes chat tools
- [ ] Claude Code, Hermes profile and opencode engines; usage-limit fallback; browser and computer-use QA
- [ ] Parallel deliveries; decomposition of large tasks into child deliveries
- [ ] Reporting: duration, token usage and rounds per node

## Safety

cpipe never merges, deploys or handles credentials. Engines work only inside the delivery's own git worktree. Pushing and opening pull
requests are done by cpipe's own code, not by the agent.

## Contributing

The test suite runs against repositories generated from templates (`tests/fixtures/make_repo.py`, Node and Python)
and a temporary Hermes board. It never touches the local `~/.hermes`. Engines are replaced with scripted fakes
(`tests/fake_codex.py`).

```sh
uv venv --python <hermes-agent venv python> .venv
uv pip install --python .venv/bin/python pytest pyyaml "agent-client-protocol>=0.9,<0.10"
# Board tests require the hermes-agent checkout on sys.path (e.g. via a .pth file); otherwise they are skipped.
.venv/bin/python -m pytest -q
```

## License

[MIT](LICENSE) © 2026 Christian Rey Villablanca
