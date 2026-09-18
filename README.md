# Hermes Delivery Workflow

**The complete software-delivery workflow for [Hermes Agent](https://github.com/NousResearch/hermes-agent) as a single plugin + repo** — orchestration policy skills, deterministic gate tools, board intelligence, and housekeeping, deployed from one place.

One repo holds 100% of the workflow. The host machine holds only placements (symlinks, script copies, cron registrations) that `install.sh` creates and re-asserts.

## What's inside

```
├── plugin.yaml                  # native Hermes plugin manifest (v2)
├── delivery_workflow/           # plugin code: 3 agent tools + doctor CLI + metrics hook
├── workflow/
│   ├── skills/                  # my-software-delivery-orchestrator + my-* policy skills
│   ├── scripts/                 # supervisor scan, validator, warm-build, housekeeping, intelligence
│   ├── cron.jobs.json           # cron job definitions (stall supervisor, watchers, digests)
│   └── config.assertions.yaml   # engine caps this workflow expects
├── install.sh                   # idempotent installer
└── tests/                       # plugin registration tests
```

## Plugin tools (agent-callable, deterministic — no LLM tokens)

- `delivery_check_policy` — validates engine caps vs team-config, roster integrity, open-card requirements
- `delivery_board_intelligence` — per-stage wall-clock, queue waits, gate rejection rates, rework loops
- `delivery_mutation_check` — flips one condition in a disposable worktree, requires the focused test to fail (proves tests bite)

Plus `hermes delivery-workflow` doctor CLI and an `on_session_end` metrics hook (append-only JSONL).

## Install / update

```sh
git clone git@github.com:chryzxc/hermes-delivery-workflow.git
cd hermes-delivery-workflow
./install.sh
hermes plugins enable delivery-workflow
```

Update on any machine:

```sh
git pull && ./install.sh
```

`install.sh` is idempotent: symlinks skills into `~/.hermes/skills` and every profile tree, copies scripts into `~/.hermes/scripts` (cron requires resolution inside that dir), merges cron job definitions by name (never touches engine-owned runtime fields), and asserts `config.yaml` matches `config.assertions.yaml`.

## Why symlinks for skills

Skills physically live here; `~/.hermes/skills/<name>` and each profile's copy are symlinks — explicitly supported by Hermes (`agent/skill_utils.py`). A pull updates every placement instantly, and per-profile skill drift becomes structurally impossible.

## Safety model

The plugin is read-only with respect to repositories and Hermes state: tools report facts and evidence, never mutate engine config or publish anything. The metrics hook appends one JSON line per session. `delivery_mutation_check` mutates only a disposable git worktree file and restores it.

## Tests

```sh
uv venv && uv pip install --python .venv "pytest>=8,<9"
.venv/bin/python -m pytest -q
```
