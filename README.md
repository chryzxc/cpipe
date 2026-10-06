# cpipe

[![CI](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml/badge.svg)](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-8957e5)](LICENSE)
[![Requires: Hermes Agent](https://img.shields.io/badge/requires-Hermes%20Agent-1f6feb)](https://github.com/NousResearch/hermes-agent)

**A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin for developers: ask for a change in chat
and get back a deliverable, a tested, QA'd and reviewed PR ready to merge.** You ask in Hermes, and cpipe puts one card on the Kanban board. A coding agent (Codex, Claude
Code, a Hermes bot or opencode) then works the whole task in **one session**. It is built on
**[graph engineering](#graph-engineering)**: code, not the agent, decides what happens next. It is also built on
**[loop engineering](#loop-engineering)**: every failure goes back into the same session, with a limit.

> **Status: v2, in progress.** v2 replaces the v1 multi-bot flow, which was too slow and too expensive (a cold
> session per step, an LLM choosing the next step). The last v1 release is the `v1-final` tag. Done so far:
> the bundled runtime, the Codex engine and setup → build ⇄ checks. The PR, review, QA and the other engines
> are next (see [Roadmap](#roadmap)).

## How a delivery flows

```
you ── Hermes chat ── ship · feedback · ready · status · stop
                 │
                 ▼
     Hermes Kanban: 1 card = 1 delivery = the state
                 │ claim (cp:<engine>)
                 ▼
  cpipe daemon → supervisor → round
                 │
  setup → build ⇄ checks → QA → security → review → push + PR → CI
   code   engine   code    engine  code    other engine    code        code
                 │
                 ▼
  card → review: "ready to test"      card → blocked: one question
  your comment → same card, SAME engine session → next round
  "ready" → final checks → ready to merge → card done
```

## Graph engineering

The delivery is a **graph written in code**, not a conversation between bots. Nodes are steps, edges are
outcomes, and `cpipe/graph.py` decides every transition with a pure function,
`next_node(node, outcome, rounds, limits)`. The model never chooses where the work goes next.

- **Two kinds of node.** *Engine* nodes (build, QA, review) are where an LLM does the work. *Code* nodes (setup,
  checks, security, push, PR, CI) are deterministic and cost no tokens.
- **The card is the state.** One Kanban card per delivery. Its status is the only interrupt: `review` means the
  deliverable is ready for you to test, `blocked` means one question, and `done` means it is ready to merge.
- **Tested like code.** Every edge and limit has a table test (`tests/test_round.py`). The whole graph also runs end
  to end against a scripted fake engine on throwaway repos.

## Loop engineering

Each loop feeds a failure **back into the same engine session**, has a fixed limit, and stops to ask you at that
limit.

| Loop | Feeds back | Limit |
|---|---|---|
| build ⇄ checks | the failing checks' output on the changed files | 3 |
| build ⇄ QA | what failed in the running app | 2 |
| build ⇄ review | the independent reviewer's findings | 2 |
| build ⇄ CI | the failing CI job | 2 |
| you ⇄ delivery | your comment on the card | you decide |

- **One session per delivery.** The engine keeps its context across every fix and every feedback round
  (`codex exec resume`, `claude --resume`), so each turn only sends the new information.
- **Pick the engine per task.** Each engine is a lane (`cp:codex`, `cp:claude`, …) with its own concurrency.

## Configure

`~/.hermes/cpipe.yaml` (every key optional):

```yaml
default_engine: codex
fallback_engine: claude
review_engine: claude
engines:
  codex:  {bin: codex, model: <model>, reasoning: medium, concurrency: 3}
  claude: {bin: claude, model: <model>, concurrency: 2}
limits: {checks: 3, qa: 2, review: 2, ci: 2, round_minutes: 90}
env_pass: [GH_TOKEN]       # extra variables the round and its engine may see
```

Per project, in `~/.hermes/delivery/`:

```yaml
# projects.yaml
projects:
  my-app:
    repo: ~/code/my-app           # cards whose worktree comes from this checkout belong to this project
    base_branch: develop
    conventions: "Run `npm test` before committing."   # first 10 lines go into the prompt
    qa: browser                    # browser | computer-use | off
    test_env: {services: {web: "npm run dev"}}

# quality.yaml: checks run on the changed files after every build
checks:
  - {name: lint, files: "*.ts *.vue", run: "npx eslint --max-warnings=0 {files}", projects: [my-app]}

# lessons.yaml: the newest 10 go into that project's build prompt ("*": every project)
my-app:
  - {text: "Run `prisma generate` before the tests"}
```

Skills and repo rules belong with the engines, not with cpipe: an `AGENTS.md` in each repo, and skills in
`~/.codex/skills` or `~/.claude/skills`.

## Run

```sh
hermes cpipe doctor    # Hermes compatibility, lanes, engines on PATH
hermes cpipe run       # the daemon: claims cp:* cards (its own process, never inside the gateway)
```

## Repo map

```
cpipe/graph.py           the graph: next_node(node, outcome, rounds, limits)
cpipe/round.py           python -m cpipe.round <task>: one round, from the card's node to an interrupt
cpipe/engines/           one coding CLI each (codex.py today)
cpipe/settings.py        cpipe.yaml → one runtime lane per engine
cpipe/quality.py         the checks node (quality.yaml)
cpipe/workspace_prep.py  the setup node: fresh worktree, linked node_modules and code index
cpipe/test_env.py        runs a branch with the project's test_env recipe (for QA)
cpipe/flow.py            per-delivery Hermes conversation
cpipe/runtime/           the bundled worker runtime: scheduler, supervisor, lifecycle, adapters
docs/runtime/            the runtime's own docs
```

## Roadmap

- [x] Bundled worker runtime, `cpipe.yaml` lanes, template fixture repos
- [x] Codex engine; setup → build ⇄ checks; feedback rounds resume the same session
- [ ] Push, PR, CI watch, security checks, public-text scan
- [ ] Independent review by a second engine; final checks; Hermes chat tools
- [ ] Claude Code, Hermes-bot and opencode engines; fallback on a quota wall; browser and computer-use QA
- [ ] Parallel deliveries; large tasks split into child deliveries
- [ ] Report: time, tokens and rounds per node

## Develop

Tests use throwaway repos built from templates (`tests/fixtures/make_repo.py`, Node and Python) and a temp Hermes
board. They never touch your `~/.hermes`. The engines are scripted fakes (`tests/fake_codex.py`).

```sh
uv venv --python <hermes-agent venv python> .venv
uv pip install --python .venv/bin/python pytest pyyaml "agent-client-protocol>=0.9,<0.10"
# add a .pth to the hermes-agent checkout so the board tests run; without it they skip
.venv/bin/python -m pytest -q
```

Merge, deploy, credentials and anything irreversible stay with you.

[MIT](LICENSE) © 2026 Christian Rey Villablanca
