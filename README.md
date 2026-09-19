# Hermes Delivery Workflow

**The complete software-delivery workflow for [Hermes Agent](https://github.com/NousResearch/hermes-agent) as a single plugin + repo** — orchestration policy skills, deterministic gate tools, board intelligence, and housekeeping, deployed from one place.

One repo holds 100% of the workflow. The host machine holds only placements (symlinks, script copies, cron registrations) that `install.sh` creates and re-asserts.

## The flow

```
            ┌──────────────────────── tier decided at intake ───────────────────────┐
            │ LOW: fast lane          MED: standard            HIGH: full ceremony   │
            ▼                                                                       │
issue ──► CLARIFY ──► EXPLORE ──► PLAN ──► PARALLEL TDD ──► REFACTOR ──► GATES ──► PR ──► LEARNING
        (intake brief   (surfaces   (frozen     (Implementer          (SIMP       (Reviewer/    (draft    (weekly
         + risk tier)    artifact)   interfaces,  instances       findings    Verifier/   PR +      digest +
                                   hash-locked)  in worktrees,   → separate  Security Reviewer on    evidence  standards
                                                 RED→GREEN)       card       frozen SHA)  block)    loop)
```

### Stages

| # | Stage | What happens | Who | Required artifact | Gate to pass |
|---|---|---|---|---|---|
| 1 | **Clarify** | Issue becomes a brief: problem, expected behavior, acceptance criteria, non-goals, surfaces, risk tier (LOW/MED/HIGH) | Coordinator (Designer only for HIGH product ambiguity) | Intake brief on the card | Brief complete — no routing on assumptions |
| 2 | **Explore** | Codebase investigation deposits the blast radius: affected files, modules, contract surfaces, cross-boundary handoffs | LOW: Coordinator direct tools · MED/HIGH: Planner (`my-repo-map`) | SURFACES section | Declared surfaces match reality |
| 3 | **Plan** | Implementation plan with **frozen interfaces**; SHA256-locked to the card | LOW: inline mini-plan · MED: Planner plan file · HIGH: + adversarial `grill-me` interrogation | Plan file + `plan_sha256` (MED/HIGH) | Plan approved + locked; amendments supersede |
| 4 | **Parallel TDD** | Implementer instances in per-issue worktrees: **RED** (failing test + output) → IMPLEMENTING → GREEN → REGRESSION | Implementer ×1–3 (LOW may batch ≤3 cards/session) | RED line before IMPLEMENTING; evidence block filling | RED evidence exists before implementation |
| 5 | **Refactor** | Simplification findings become their own card — never mixed with behavior changes | Implementer (`simplify-code`) | Separate simplify card, tests green before/after | Zero behavior change in the diff |
| 6 | **Gates** | Independent review of the **frozen SHA**: diff review, matrix reproduction, security when triggered | LOW: Reviewer one-shot · MED: dispatched Reviewer · HIGH: Reviewer + Verifier + Security Reviewer in parallel | Complete evidence block (RED + matrix + mutation line on HIGH) | Most severe verdict wins; undeclared file = REQUEST_CHANGES |
| 7 | **PR** | Draft PR generated from verified evidence; approvals batched per wave | Coordinator (`creating-pr-content`) | PR body renders the evidence block | Your one `ship N / hold N` reply per wave |
| 8 | **Learning** | Metrics, gate effectiveness, finding classes → weekly digest; repeated findings draft standards amendments | Deterministic scripts + Auditor | Weekly digest (`board-intelligence.md`) | You approve standards drafts only |

### Tiered ceremony (token economics)

The intake tier decides how much process each card earns:

| | LOW | MED | HIGH |
|---|---|---|---|
| Trigger | ≤2 files, no contract change, existing coverage | behavior or UI change | schema/API/security/trust boundary |
| Plan | inline mini-plan | file + SHA lock | + `grill-me` interrogation |
| Review | same-card Reviewer (one-shot OK) | dispatched Reviewer | Reviewer + Verifier + Security Reviewer parallel |
| Matrix reproduction | Implementer's block + Reviewer diff check | Verifier re-runs criteria | + mutation line |

LOW cards touch ~3 bots in ~2 sessions (skip the plan session and Verifier reproduction — the bulk of the token savings). HIGH earns full ceremony.

### The evidence block (per card)

```
RED LINE      failing test name + output, posted before IMPLEMENTING
MATRIX        criterion | exact command | result | gate owner
MUTATION      one flipped condition, one matrix test must fail (HIGH tier)
```

COMMIT_READY requires the complete block. The PR body renders it — **you review evidence, not code.**

### The learning loop

`delivery_board_intelligence` (weekly cron) reports per-stage wall-clock, queue waits, gate rejection rates, rework loops, and finding classes. A Reviewer/Verifier finding class appearing 3+ times auto-drafts an amendment to `my-engineering-standards` — the workflow writes its own rulebook. A gate silent for two weeks is flagged fix-or-remove.

## Bring your own bots

This repo contains **no bot names**. Every participant is a role — Coordinator, Implementer, Reviewer, Verifier, Security Reviewer, Planner, Researcher, Designer, Release Engineer, Spike Explorer, Security Tester, Auditor, Administrator. Map them to any Hermes profiles you have (any names, any models) in `workflow/roster.yaml` (copy `roster.example.yaml`; gitignored, deployed to `~/.hermes/roster.yaml`). Skills, prompts, and validators resolve roles through that file, so a different team setup works without touching the workflow.

## What's inside

```
├── plugin.yaml                  # native Hermes plugin manifest (v2)
├── delivery_workflow/           # plugin code: 3 agent tools + doctor CLI + metrics hook
├── workflow/
│   ├── skills/                  # my-software-delivery-orchestrator + my-* policy skills
│   │   └── my-software-delivery-orchestrator/references/   # routing, gates, handoffs, task graph
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

## Install / update — one command

Any machine with Hermes installed:

```sh
bash <(curl -fsSL https://raw.githubusercontent.com/chryzxc/hermes-delivery-workflow/main/bootstrap.sh)
```

That clones (or updates) the repo, installs everything, and enables the plugin. On first run you answer **5 quick questions** mapping roles to your profiles (coordinator, implementer, reviewer, verifier, security-reviewer) — Enter accepts defaults, everything else is auto-aliased. Prefer flags or have all 13 specialists? Skip the prompts:

```sh
./install.sh --roster coordinator=default implementer=forge reviewer=sentry verifier=sentinel security_reviewer=cypher
# or the full 13-role form — rerun with --force anytime to redo
```

Update on any machine:

```sh
git pull && ./install.sh
```

`install.sh` is idempotent and self-contained: symlinks skills into `~/.hermes/skills` and every profile tree, copies scripts into `~/.hermes/scripts` (cron requires resolution inside that dir), merges cron job definitions by name (never touches engine-owned runtime fields), asserts `config.yaml` matches `config.assertions.yaml`, sets up `~/.hermes/roster.yaml`, and enables the plugin.

## Why symlinks for skills

Skills physically live here; `~/.hermes/skills/<name>` and each profile's copy are symlinks — explicitly supported by Hermes (`agent/skill_utils.py`). A pull updates every placement instantly, and per-profile skill drift becomes structurally impossible.

## Safety model

The plugin is read-only with respect to repositories and Hermes state: tools report facts and evidence, never mutate engine config or publish anything. The metrics hook appends one JSON line per session. `delivery_mutation_check` mutates only a disposable git worktree file and restores it. Human approval gates are unchanged: merge, deploy, production, credentials, purchases, publishing, irreversible deletion.

## Tests

```sh
uv venv && uv pip install --python .venv "pytest>=8,<9"
.venv/bin/python -m pytest -q
```
