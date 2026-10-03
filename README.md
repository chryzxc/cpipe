<p align="center">
  <img src="assets/banner.svg" alt="cpipe — clarify → explore → plan → parallel TDD → gates → PR → learning">
</p>

# cpipe

**cpipe is the workflow for your AI dev team in [Hermes Agent](https://github.com/NousResearch/hermes-agent) — shipped as one plugin.** It routes each request through your own profiles (plan → build → review → verify) and gates every stage on evidence, ending in a reviewed, tested PR. Requests are clarified first, built with TDD in worktrees, reviewed on frozen commits, and verified by actually running the tests.

> **cpipe runs the team; you bring the members.** The plugin decides *which role* gets each card, *what the card asks for*, and *what evidence a stage must produce*. It does not provide the bots. The quality of the result depends on the Hermes profiles you map to each role: their model, their persona (`SOUL.md`), their role skills, and their toolsets. You configure those; see [Bring your own bots](#-bring-your-own-bots).

[![Install: one command](https://img.shields.io/badge/install-one%20command-238636)](#-quickstart)
[![CI](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml/badge.svg)](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-8957e5)](LICENSE)
[![Requires: Hermes Agent](https://img.shields.io/badge/requires-Hermes%20Agent-1f6feb)](https://github.com/NousResearch/hermes-agent)
[![Profiles: bring your own](https://img.shields.io/badge/bots-bring%20your%20own-f0883e)](#-bring-your-own-bots)

---

## The idea in one paragraph

This workflow is a **composition of established engineering paradigms**, not a pile of prompts. **Task-graph (DAG) engineering** decomposes issues into dependency-aware nodes that execute in parallel waves. **Loop engineering** nests four closed feedback loops — the RED→GREEN TDD loop inside a card, the implement→review→rework loop across cards, the supervisor's sense→act loop every 15 minutes, and the weekly learning loop that writes the rulebook. **Evidence-based gating** makes every claim fail-closed and machine-verifiable, **risk-tiered ceremony** scales process weight to blast radius, and **policy-as-code** keeps deterministic checks out of the LLM path. Together they turn a set of AI profiles into a delivery team whose output you can trust without reading every diff.

## The problem it solves

Handing work to a team of AI agents fails in two quiet ways.

- **You can't trust what they ship.** An agent says "done" and "tests pass"; nothing proves it.
- **You can't trust that they're still working.** A chain of agent tasks stops on a quota limit, a dead worker, a
  retry loop or a missing dependency, and nothing tells you. The chat says "queued, will resume automatically" while no
  one is running. You end up asking "any update?" all day, which is exactly the babysitting you wanted to get rid of.

This plugin addresses both. Every stage has to produce evidence a script can check. And the board is watched by
deterministic code that believes the task database, not the agents' own status messages.

## What this workflow solves

AI teams move fast; trusting what they ship is the hard part. This workflow turns every delivery claim into something you can verify:

| You get | How it works |
|---|---|
| Parallelism you can trust | Independent slices run as parallel waves by default — wave plans computed from the engine's live caps, so what's promised is what runs |
| TDD by construction | RED evidence is required *before* implementation; mutation checks prove the tests bite |
| Reviews that can't drift | Gates review a frozen SHA; any base or head movement invalidates prior evidence |
| `done` means proven | After review, a Verifier card runs the change: the new tests must fail without the fix, the related tests and the CI suite must pass. A failure opens a fix card automatically |
| One source of truth | Skills deploy as symlinks from a single git-versioned repo — identical across every profile by construction |
| A board that runs itself | A deterministic supervisor keeps queues flowing, reclaims dead claims, and routes decisions to the coordinator |
| A readable conversation list | Session hygiene runs every 30 minutes: completed cron ticks are archived, stale open `cron_*` ticks are removed, and coordinator wakes are tagged as integration sessions hidden from user lists |

The result: **you review evidence, not code.**

## The flow

```
issue ──► clarify ──► explore ──► plan ──► PIN ──► TDD ──► review ──► verify ──► PR ──► learning
        intake brief   surfaces    frontier   tests    RED→    frontier   PROOF +     evidence   weekly digest
        + risk tier    artifact    model:     for      GREEN   model:     RELATED +   block +    + standards
                                  change +   today's          callers +  SUITE       approvals  loop
                                  pins       behavior         pins
                                                                  ▲           │ fail
                                                                  └── fix ◄───┘  (max 2 rounds, then you decide)
```

### Stages

| # | Stage | What happens | Required artifact | Gate to pass |
|---|---|---|---|---|
| 1 | **Clarify** | Issue becomes a brief: problem, acceptance criteria, non-goals, risk tier (LOW/MED/HIGH) | Intake brief | Brief complete — no routing on assumptions |
| 2 | **Explore** | Blast-radius investigation: affected files, modules, contract surfaces | SURFACES section | Declared surfaces match reality |
| 3 | **Plan** | Implementation plan with frozen interfaces, SHA256-locked to the card | Plan file + hash (MED/HIGH) | Plan locked; amendments supersede |
| 3b | **Pin** | Before touching source, the implementer writes a test for every caller behavior the plan marked UNPINNED, asserting what the code does *today*. They must pass on the unchanged code | `PINNED:` test list | Verify re-runs them at the merge-base |
| 4 | **Parallel TDD** | Implementer instances in per-issue worktrees: RED → IMPLEMENTING → GREEN → REGRESSION, with every pin still green | RED line before implementation | RED evidence exists |
| 5 | **Refactor** | Simplification findings become their own card — never mixed with behavior | Separate simplify card | Zero behavior change |
| 6 | **Review** | Independent review of the frozen SHA: diff, every caller of every changed symbol or route, security when triggered | `CALLERS CHECKED` list | No approval without the list |
| 6b | **Verify** | The Verifier runs the change in the same worktree: PROOF (changed tests fail at the merge-base), RELATED (tests of every changed file, every package), SUITE (repo CI commands) | Command + result per step | Any failure → `delivery_verify_failed` opens a fix card and a new verify card |
| 7 | **PR** | Draft PR generated from verified evidence; approvals batched per wave | PR with evidence block | Your one `ship N / hold N` reply |
| 8 | **Learning** | Weekly digest: flow metrics, gate effectiveness, finding classes → standards amendments | Weekly digest | You approve drafts only |

### Risk-tiered ceremony

Not every change earns the same process. The intake tier decides:

| | LOW | MED | HIGH |
|---|---|---|---|
| Trigger | ≤2 files, no contract change, existing coverage | behavior or UI change | schema/API/security boundary |
| Plan | the implementer, on its own card: CHANGE + PINS + RED | cheap map → frontier plan | + adversarial interrogation |
| Review | same-card reviewer (one-shot) | dispatched review | reviewer + QA + security in parallel |
| Verify card (PROOF/RELATED/SUITE) | on request (`verify: true`) | on request | + mutation check |

A feature is one card: the implementer plans, builds, tests, pushes, and opens a draft PR with a `## How to test` checklist; the reviewer checks the same card (at most 3 rounds) and marks the PR ready. You test the PR and merge, or send `fix <PR> <issue>`, which reopens the same branch. Every worker sees only its card (the request brief plus the parent card's result), never the chat, so each session stays small.

## The concepts behind it

This isn't a pile of prompts — it's several established engineering paradigms composed into one delivery system. Knowing the vocabulary makes it easier to adopt, adapt, and trust:

| Concept | How it shows up here |
|---|---|
| **Orchestrator–worker architecture** | A coordinator role is the control plane: it routes, reconciles, and reports — never implements. Workers are disposable instances of role profiles, spawned per card by the engine dispatcher. |
| **Task-graph (DAG) engineering** | Work is decomposed into dependency-aware task nodes (`references/task-graph-flow.md`). Ready nodes execute in parallel waves; integration happens before gates, so reviews see integrated reality, not divergent branches. |
| **Loop engineering (closed feedback loops)** | Four nested loops: the RED→GREEN TDD loop inside a card; the implement→review→rework loop bounded by gates across cards; the supervisor's sense→act loop every 15 minutes; and the weekly learning loop that turns repeated findings into standards. |
| **Autonomic operation** | A deterministic supervisor keeps the board healthy on its own schedule — reclaims dead claims, refreshes queues, and routes cards that need a decision to the coordinator. |
| **Evidence-based gating (fail-closed)** | Nothing passes without machine-verifiable evidence: criteria re-run independently, frozen-SHA review, hash-locked plans, mutation checks. Missing evidence is a blocker, never a pass. |
| **Risk-tiered adaptive ceremony** | Process weight scales with blast radius — LOW/MED/HIGH tiers decided at intake from the brief's own fields, so small changes move fast and dangerous ones earn full scrutiny. |
| **Pipelined parallelism** | Independent slices decompose into parallel waves by default — no prompt needed; reviews of one wave overlap implementation of the next; same-module cards batch into one session to amortize boot cost. Parallelism comes from *instantiation* of roles, not from more bots. |
| **Pull system with WIP limits (Kanban)** | Per-profile concurrency caps, queue signals, and wave plans computed from live engine caps — never from wishful policy numbers. |
| **Deterministic-first / policy as code** | Anything expressible as a script runs without an LLM (validators, metrics, mutation checks, housekeeping); config assertions fail loudly on drift; declarative policy mirrors are validated against engine reality. |
| **Role-based least authority** | Authority ceilings attach to roles and cards; no skill, card, or model can expand them. Human approval gates (merge, deploy, credentials...) are structural, not conventional. |
| **Single-source-of-truth deployment** | The whole workflow is git-versioned and deployed idempotently — skills are symlinks, so drift across profiles is structurally impossible and `git pull && ./install.sh` is the only update path. |

## 🚀 Quickstart

Requires a working [Hermes Agent](https://hermes-agent.nousresearch.com/docs/) install with at least one profile (bot).

```sh
bash <(curl -fsSL https://raw.githubusercontent.com/chryzxc/cpipe/main/bootstrap.sh)
```

Answer **5 questions** mapping roles to your profiles (Enter accepts defaults; the rest auto-alias). Prefer flags?

```sh
git clone https://github.com/chryzxc/cpipe.git
cd cpipe
./install.sh --roster coordinator=default implementer=forge reviewer=sentry verifier=sentinel security_reviewer=cypher
```

Updating: `git pull && ./install.sh` — idempotent, one step, updates skills, scripts, cron, and the plugin everywhere.

## 🤖 Bring your own bots

This repo contains **no bot names** — it ships the workflow, not a roster. Every participant is a role (Coordinator, Implementer, Reviewer, Verifier, Security Reviewer, Planner, Researcher, Designer, Release Engineer, Spike Explorer, Security Tester, Auditor, Administrator). Map them to any Hermes profiles in `~/.hermes/roster.yaml`:

```yaml
roles:
  coordinator: default      # your main agent
  implementer: my-coder     # any name you use
  reviewer: my-reviewer
  # ... 5 required, 8 optional (auto-aliased)
```

Works with 3 profiles or 13. Different team setups adopt the same workflow without touching it.

### What you configure (the workflow does not)

The plugin writes each card's brief, but a worker is still *your* profile. For every mapped role, set up:

| Per profile | Why it matters |
|---|---|
| **Model** (`config.yaml`) | Planner and reviewer make the judgment calls (what to pin, whether callers are safe): give them a frontier model. Investigator, implementer and verifier follow instructions and run commands: a cheap model is enough |
| **Persona** (`SOUL.md`) | The bot's standing rules. It must not contradict the card brief (for example, an implementer SOUL that says "run only focused tests" undoes the brief's "run related tests") |
| **Role skills** | The procedure the bot follows for its role (review checklist, TDD loop, verification steps) |
| **Toolsets and disabled skills** | Fewer tools and skills means a smaller prompt and a faster, more focused worker |
| **Quality checks and guidance** (`delivery/quality.yaml`) | Your linters run before every review; your skill and design rules reach each role's first turn (see Setup step 5) |

### Example bots

`workflow/bots/<role>/` holds the author's own personas and role skills (implementer, reviewer, verifier, investigator, planner), written to match the briefs. `./install.sh` copies them into the profile each role maps to, backing up any file it replaces as `*.bak-install-<timestamp>`. Roles without a mapped profile are skipped. To keep your own personas, run `SKIP_BOTS=1 ./install.sh`, and use these files as a reference for what your bots need to do.

## What's inside

```
├── plugin.yaml                  # native Hermes plugin manifest (v2)
├── cpipe/           # plugin: agent tools + doctor CLI + hooks
├── dashboard/                   # Mission Control tab for `hermes dashboard`
├── workflow/
│   ├── bots/                    # example personas + role skills, copied into mapped profiles
│   ├── skills/                  # orchestrator policy skill + evidence/ADR/standards skills
│   ├── scripts/                 # board supervisor scan, warm-build, housekeeping, session cleanup, intelligence...
│   ├── cron.jobs.json           # 9 cron jobs: delivery monitor, board supervisor, triage, watchers, digests, session hygiene
│   ├── config.assertions.yaml   # engine caps this workflow expects
│   └── roster.example.yaml      # role → profile mapping template
└── install.sh                   # idempotent installer (bootstrap.sh = one-liner)
```

## Plugin tools (agent-callable, deterministic — zero LLM tokens)

- `delivery_check_policy` — validates engine caps vs policy, roster integrity, open-card requirements
- `delivery_board_intelligence` — per-stage wall-clock, queue waits, gate rejection rates, rework loops
- `delivery_mutation_check` — flips one condition in a disposable worktree and requires the focused test to fail
- `delivery_submit` — turns a request brief (max 3000 chars) into one build card with a same-card review (map → plan → build for large work; then a verify card unless `verify: false`, routed to the release engineer when CI, deps, containers, or migrations change), or with `fix_of` a fix card on an existing card's branch, and subscribes the chat to every card. The implementer cannot complete its own card: a `pre_tool_call` gate sends it to review. A clarify from a background (headless) coordinator turn is queued as a question on the card for Herm to show instead of being auto-answered
- `delivery_verify_failed` — called by the Verifier on a failed verify card; opens a fix card in the same worktree plus a fresh verify card, and stops after 2 rounds
- `delivery_status` — the board's truth: each open card's verdict computed from the task database now (`PROGRESSING`, `WAITING(<named thing>)`, or `STUCK(<cause>)` with the next step). The coordinator answers every status question from it
- `delivery_test_env` — runs a card's or PR's branch for the operator to test with the project's saved `test_env` recipe (projects.yaml) under wtg; returns the URLs and the commit they serve, or the failing service's log
- `delivery_watch` — registers a PR or CI run the monitor polls for a card; a pass unblocks the card, a failure or expiry tells the chat. Required before anyone says a card "will resume"

Plus the `hermes cpipe` CLI (`doctor`, `queue`, `status [--card ID]`, `log [--card ID] [--since 2h] [--kind K]`, each with `--json`), an `on_session_end` metrics hook (append-only JSONL), and the liveness hooks described below.

**Worker hooks** (deterministic, no LLM tokens):

- **Workspace prep.** On a worker's first turn in a fresh worktree, the plugin symlinks the main checkout's `node_modules`. If the repo has a [codegraph](https://github.com/colbymchenry/codegraph) index (`.codegraph/`), it gives the worktree a copy-on-write clone of that index, which syncs to the branch in the background. If `testscope` is on `PATH`, it tells the worker to run `testscope --base origin/<base>` before the full suite. testscope runs only the tests the change touches, names hung files and marks failures new or pre-existing.
- **Public text gate.** PR titles, bodies and comments are public, and so are commits. A delivery worker's `gh pr create/edit` is refused when its text contains local paths, card ids, review ids, delivery tokens, SHAs or bot names. So is a `git push` whose unpushed commits carry those in their messages or added lines. Workers never comment on or review PRs on GitHub. Tokens and paths match by case, so prose like "pinned" and `/users/` routes pass.

## Liveness & autonomy

The chain is meant to run from plan to final report without you asking "what's next?". The pieces that keep it moving:

| Piece | What it does |
|---|---|
| Gateway dispatcher | Every 15s it claims `ready` cards **and** `review` cards (native same-card review). Linked children are promoted automatically when their parents finish, so pre-created chains need no coordinator turn between steps. |
| Dispatch telemetry | The plugin's `on_kanban_dispatch_tick` hook writes `~/.hermes/logs/dispatch-health.json`: last tick, last spawn, and why each held card was held (per-profile cap, respawn guard, unassigned…). |
| Delivery monitor | Every 5 minutes a deterministic script reads the task database, worker processes and GitHub directly and gives every open card exactly one verdict: *progressing* (a live worker), *waiting on something named* (a PR check, a quota reset, your decision, a parent card), or *stuck* with a cause (`DEAD_WORKER`, `STALE_GUARD`, `QUOTA_WALL`, `AUTH_BLOCKED`, `IDENTICAL_FAILURE`, `RUN_BUDGET`, `ORPHAN_REVIEW`, …). Known stuck states are repaired (reclaim a dead claim, clear a stale respawn guard, pause cards behind a quota wall until the provider's reported reset (`hermes usage`) and resume them, re-subscribe a child to its chain's chat). The rest are escalated once: the card is blocked with `<CAUSE>: <why>; next: <what unsticks it>`, which notifies the chat that started the work, or, for cards that cannot be blocked, a notice is handed to that chat's next turn. A stuck parent owns its children's stall, so you hear about the root, not every card behind it. It also posts a digest when something newly stalls or starts moving again. |
| Board supervisor | A cheap scan every 15 minutes for decisions that need judgment: verdicts parked in block reasons, rework loops, orphaned chains, unsubscribed cards. Its output is byte-stable, so the LLM only runs when something changed. It never dispatches: the gateway is the only dispatcher. |
| Board triage | Every 20 minutes a deterministic cron retries blocked cards that only hit a rate limit, timeout, or worker crash (twice at most, never while the provider is still rate limiting) and moves reason-less blocked children back to wait on their parent. Once a day, or when a new decision is needed, it sends you a digest grouped as *needs your decision / looks done / blocked by old rules / parked*. Reply `continue <id> <what to do>`, `archive <id>` or `resubmit <id>` and the coordinator acts on it. |
| Mission Control | A dashboard tab (`hermes dashboard` → Mission Control) with the same groups. Open a card to read its reason, body and latest comments, then Continue with a note, Resubmit it fresh under the current flow, or Archive. Tick several for bulk actions. `hermes cpipe queue` prints the same queue as JSON for other clients (Herm uses it). |
| Chat hooks | `pre_llm_call` delivers the monitor's notices for this chat, tells the next turn when ESTOP is holding work, and, when you ask "any update?", makes the coordinator answer from `delivery_status`. `post_llm_call` checks a reply that says a card is moving against the board and makes the coordinator correct itself when it is not. `pre_approval_request` blocks a worker's card with `APPROVAL_NEEDED` instead of letting it wait on a prompt nobody sees. |

**Emergency stop.** `~/.hermes/ESTOP` (for example the Dock's pause control) pauses the dispatcher **and every cron job, including the stall supervisor**. Work stops on purpose and nothing reports it, except the session notice and the doctor. Agents are told never to lift it and never to bypass it with `hermes kanban dispatch`, because that manual pass ignores ESTOP. Run `hermes resume` when you want work to continue.

**Troubleshooting a stalled board:** run `hermes cpipe status` for every card's verdict, `hermes cpipe log --card <id>` for one card's full story from the delivery journal (`~/.hermes/logs/delivery-journal.jsonl`), and `hermes cpipe` (the doctor) for ESTOP state, the dispatcher's last tick and spawn, and current holds. The hooks only load after `./install.sh` and a gateway restart.

## Trust the board, not the messages

The goal is a chain that runs from request to final report with **zero "any update?" messages**. Agent status replies
and engine health signals can be wrong. The task database, the worker processes and GitHub are not, so the plugin
reads those itself:

| Piece | What changes for you |
|---|---|
| Delivery journal | One JSON line for every decision the workflow makes (repairs, escalations, retries, nudges, wasted runs). `hermes cpipe log` reads a card's full story. `workflow/scripts/delivery_baseline.py` measures the autonomy numbers from it. |
| Truth monitor | Every open chain is progressing, waiting on something named, or it has told you. |
| Honest status | Status answers come from `delivery_status`, never from memory. "Will resume" requires a `delivery_watch`. |
| Retries with memory | The same failure signature twice means stop and ask, not a tenth retry. More than 10 runs, or 3 fast failures in a row, stops retries. |
| No doomed work | A duplicate submission returns the existing card, a hand-made "Review …" card is refused, and workspace prep flags a branch or base that breaks the project's rules. |
| Your rules stick | Base branch, branch naming, environment setup and conventions (draft-PR timing, forbidden tools) are saved per project and stamped into every card. |
| Clean backlog | An undecided card gets one reminder at 7 days, then is archived at 10 with a final comment (only after that reminder). |

### Setup

1. **Install or update**, then restart the gateway so the new tools and hooks load:

   ```bash
   git pull && ./install.sh
   hermes gateway restart
   ```

   `./install.sh` copies the monitor into `~/.hermes/scripts/` and registers it as the `Delivery monitor` cron job
   (every 5 minutes). Check it is there with `hermes cron list`. PR/CI watches need an authenticated `gh` CLI.

2. **Let it observe first.** Out of the box the monitor runs in `observe` mode: it classifies every card, sends the
   stall digest and journals what it *would* repair (`monitor.would_repair`), but changes nothing on the board.
   After a few days, read what it would have done:

   ```bash
   hermes cpipe log --kind monitor.would_repair --since 3d
   ```

3. **Turn on repairs** once that list looks right. Create `~/.hermes/delivery/config.yaml`:

   ```yaml
   monitor_mode: act             # observe (default) | act
   ```

   The next tick picks it up; no restart needed. While ESTOP is engaged the monitor only observes, whatever this says.

4. **Save your project rules** (optional, but it is how "PRs target develop" stops being forgotten). Create
   `~/.hermes/delivery/projects.yaml`, keyed by the project name you pass to `delivery_submit`:

   ```yaml
   projects:
     my-app:
       base_branch: develop        # every card's worktree must contain origin/<base_branch>
       branch_prefix: feat/        # branch naming
       env: "cp .env.example .env" # setup notes told to workers
       conventions: "draft PR at the first pushable commit; never use some-tool"   # copied into every brief
   ```

   You rarely edit this by hand: when you state a rule in chat ("this repo's PRs go to develop"), the coordinator saves
   it here before replying. Every new card carries these rules, and workspace prep flags `CONVENTION_MISMATCH` when a
   worktree is on the wrong base or branch.

5. **Plug in your quality setup** (optional). cpipe ships no linters, skills, or style rules: your checks and your
   per-role guidance live in `~/.hermes/delivery/quality.yaml`, and the workflow decides when they apply:

   ```yaml
   checks:            # run in the card's worktree when the implementer requests review
     - name: lint
       files: "*.js *.ts *.vue"                  # globs; the check is skipped when no changed file matches
       run: "npx eslint --max-warnings=0 {files}" # {files}: the changed files that matched; {base}: the diff base sha
       projects: [my-app]                        # optional: only cards of these projects
   guidance:          # added to the first turn of every card worked by the profile mapped to that role
     implementer: "For new screens load skill `my-ui-skill`; otherwise reuse the app's components and tokens."
     reviewer: "Also check: duplicated logic, unbounded lists, swallowed errors, loading/empty/error states."
     verifier: "For UI cards, run my accessibility scan on the changed pages."
   ```

   A check that exits non-zero refuses the review request and shows the implementer its output (the last 2500
   characters), so the reviewer never spends a round on what a command can find. Checks run on the changed files only
   (a fix round from its `FIX BASE`, a build from where it left the base branch). A check that runs past 180 seconds
   is skipped, and after 3 refusals on one card the request goes through, so a broken check never wedges delivery. The
   implementer's first turn lists the checks so it runs them before asking. Keep tool- and skill-specific rules here,
   not in the bot SOULs: the same workflow then serves a team with different skills.

6. **Measure** (optional): `python3 ~/.hermes/scripts/delivery_baseline.py 7` prints the autonomy numbers for the
   last 7 days (spawns per completed run, cards over the run budget, undecided cards, nudges per delivered card). Run
   it before switching to `act` and again a week later to compare.

### Using it

**In chat** you don't call anything yourself. Ask "any update?" or "what's the status of the login fix?" and the
coordinator answers from `delivery_status`, one line per card:

```
t_1a2b3c4d  Fix login redirect   STUCK(DEAD_WORKER)   next: reclaimed; the dispatcher respawns it
t_5e6f7a8b  Review login fix     WAITING(ci https://github.com/org/my-app/pull/42)
t_9c0d1e2f  Plan settings page   PROGRESSING
```

When a card stops, you hear about it without asking: the card is blocked with a one-line reason and next step, which
notifies the chat that started the work, or the reason is added to that chat's next turn. Reply with what to do
(`continue <id> <instruction>`, `archive <id>`, `resubmit <id>`) as with the triage digest. If the coordinator says a
card "will resume" after a PR merges or CI passes, it has registered a `delivery_watch`; when the PR merges the card is
unblocked, and when CI fails or the watch expires you are told.

**From the terminal:**

```bash
hermes cpipe status                      # every open card's verdict, stuck first
hermes cpipe status --card t_1a2b3c4d    # one card, or a whole chain by its root id
hermes cpipe log --card t_1a2b3c4d       # that card's full story: repairs, escalations, retries, plus the
                                         # bots' board events (claims, blocks, comments) for its whole chain
hermes cpipe log --since 2h --kind waste.  # wasted runs in the last 2 hours
hermes cpipe log --kind monitor.escalate --json   # raw JSON, for scripts
```

The journal itself is `~/.hermes/logs/delivery-journal.jsonl`, one JSON line per decision.

**Reading a verdict:**

| Verdict | Meaning | What happens |
|---|---|---|
| `PROGRESSING` | A live worker holds the card | Nothing |
| `WAITING(<thing>)` | Waiting on something named: your decision (including a worker's `APPROVAL_NEEDED`), a PR/CI watch, a quota reset, a parent card, a scheduled time | Nothing until that thing changes; undecided cards get one reminder at 7 days and are archived at 10 |
| `STUCK(<cause>)` | Stopped, with a cause | Repaired when the cause is known and safe (`DEAD_WORKER`, `STALE_GUARD`, `QUOTA_WALL`, `UNSUBSCRIBED`), otherwise escalated once with the next step (`AUTH_BLOCKED`, `IDENTICAL_FAILURE`, `RUN_BUDGET`, `FAST_FAIL`, `ORPHAN_REVIEW`, …) |

Design and rationale: [`docs/plans/2026-09-29-autonomy-plan.md`](docs/plans/2026-09-29-autonomy-plan.md).

## The learning loop

**Did a brief change help?** `python3 -m cpipe.brief_replay --stage plan --limit 2` re-runs past done Map/Plan cards on their own profiles, using the brief `submit.py` writes today. Each runs in a detached worktree at the commit the card started from, and the output compares old and new tool calls, tokens and cost. Only the read-only stages are replayed. Replays spend real quota; `--dry-run` compares brief sizes only.

The weekly digest reports per-stage wall-clock, queue waits, gate rejection rates, and finding classes. Any review finding class appearing 3+ times auto-drafts an amendment to the shared PRINCIPLES block — **the workflow writes its own rulebook**. A gate silent for two weeks is flagged fix-or-remove.

## Safety model

- `delivery_check_policy`, `delivery_board_intelligence`, and `delivery_mutation_check` are read-only; `delivery_submit` and `delivery_verify_failed` only create Kanban cards and subscriptions
- The board supervisor writes only Kanban cards and profile skill installs; repositories remain untouched
- `./install.sh` writes the example bots into your mapped profiles unless `SKIP_BOTS=1`, and backs up every file it replaces
- Human approval gates are unchanged and always yours: merge, deploy, production, credentials, purchases, publishing, irreversible deletion
- `delivery_mutation_check` mutates only a disposable git worktree file and restores it
- `cpipe.brief_replay` runs in a temporary detached worktree, which it removes afterwards. It replays only Map/Plan cards, which push nothing
- The engine install (`~/.hermes/hermes-agent`) is never modified — everything lives in the user-state layer and survives `hermes update`

## FAQ

**Does it work with fewer than 13 profiles?** Yes — 5 required roles, the rest alias automatically. Even 3 profiles work (roles share profiles).

**Does one implementer bot serialize work?** No — a bot is a profile, and workers are disposable instances of it. The engine runs up to `max_in_progress_per_profile` concurrent workers per bot and the dispatcher alone decides spawning; coordination agents never block ready work for capacity (the supervisor detects and requeues such mistakes).

**Will `hermes update` remove it?** No. Everything installs into the Hermes user-state layer; the updater only touches the engine checkout. Run `./install.sh` after an update to re-assert everything.

**Does it cost more tokens?** Less, usually: tiering skips the plan session for low-risk work, the verify card runs commands instead of re-reading code, reviews batch multiple commits per session, and every check expressible as a script runs without an LLM.

**Can I use it on multiple machines?** Clone, `./install.sh`, done — same workflow everywhere.

**Is there a CI badge?** Yes — GitHub Actions runs the full pytest suite on every push and PR to `main`.

## Contributing

Issues and PRs welcome. Run the tests before submitting:

```sh
uv run --no-project --with "pytest>=8,<9" --with pyyaml python -m pytest -q
```

## License

[MIT](LICENSE) © 2026 Christian Rey Villablanca
