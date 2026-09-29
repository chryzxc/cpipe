# Autonomy plan: a delivery chain that finishes without being asked

Status: **phases 0–8 implemented in one PR**, monitor in `observe` mode by default. Where the implementation
differs from the plan below:

- **Supervisor input:** the LLM job keeps `kanban-supervisor-scan.py` as its monitor script instead of
  `delivery_monitor.py --judgment-queue`. The scan lost every deterministic handler (READY_STUCK, REVIEW_STALLED,
  QUEUED_AT_CAP, DISPATCHER_SILENT, HEARTBEAT_STALE, STALE_CLAIM, TRIAGE_PARKED, PROGRESS_DELTA) to the monitor and
  prints no ages, so its output is byte-stable and cron's output hash skips the LLM on unchanged ticks. The prompt is
  under 3k characters, dispatches nothing, and a test checks each rule appears once and every scan signal has one.
- **Coordinator wake:** cards that can be blocked are escalated on the card. Cards that cannot (done, review) get at
  most one batched `hermes -p <coordinator> -z` invocation per tick, without `--source tool`.
- **Review cards cannot be blocked.** Their escalation reaches the origin chat as a notice on its next turn (the
  `pre_llm_call` hook) plus the stall digest, not as a notify+wake block event.
- **Project profiles** are keyed by the project name cards already carry (`projects.<name>`) with `base_branch`,
  `branch_prefix`, `env` and `conventions`. Draft-PR timing and forbidden tools live in `conventions` as text; chain
  branch reuse and `autonomy: full` on the chain root are not implemented.
- **Approval prompts:** the `pre_approval_request` hook blocks a worker's card with `APPROVAL_NEEDED`; the
  `config.assertions.yaml` check for prompt-free worker profiles is not added.
- **Phase 6:** the orchestrator skill's contradictions are fixed (no manual dispatch, status from `delivery_status`,
  "will resume" needs `delivery_watch`) and the notice/handoff rules added, but it is not yet split to ~8k characters.
- **Expiry:** archive at 10 days requires an `EXPIRING` notice recorded at least 3 days earlier, so switching to
  `act` never archives old cards cold.
Scope: this plugin only. When a problem traces to Hermes itself, the plugin still detects it and works around it; any
upstream report is separate and optional.

## 1. The problem

A delivery chain (plan → build → review → verify → PR) should run from the request to the final report on its own. In
practice it stops silently, and the operator only finds out by asking "any update?". A two-week audit of a real board
found the same failure shapes again and again:

| Symptom | What the operator sees | Where it really comes from |
|---|---|---|
| Silent stall | Card sits in `review`/`ready`, nothing running, chat says "queued" | An engine hold (respawn guard, quota wall, dead worker) that nobody reports to the chat that started the work |
| False progress | Coordinator says "pending / will resume automatically" | The claim is not backed by any claimed run or registered watch |
| Retry loops | Many runs, same error | Retries don't remember the previous error and don't escalate |
| Doomed spawns | Worker starts, fails in seconds | Duplicate card, missing deps, wrong worktree, scope that doesn't exist, review filed as a normal card |
| Lost instructions | Wrong base branch, new branch per phase, wrong branch name, unrequested tools | The operator's rules live in chat text; cards and workers never see them |
| Silent triage | Rework loop ends in `triage`, which never restarts | The loop safeguard parks the card without telling anyone why |
| Blocked on approvals | Unattended worker waits on an approval prompt | Nobody is there to answer it |
| Chat bloat | Hundreds of turns, dozens of compactions | Every notice and every "continue" lands in one chat |
| Noise | Supervisor raises "dispatcher silent" almost every tick | The check reads stale telemetry and answers with a manual dispatch |
| Backlog | Dozens of blocked/triage cards, weeks old | Nothing expires or forces a decision |

Rough scale from that sample: about 6 worker spawns per completed run, reviewer quota walls costing 35+ respawns on a
single card, and roughly 1 in 3 operator messages being a nudge.

Every fix so far patched one path (an issue per symptom). This plan fixes the shape instead.

## 2. Design principle: the plugin owns the truth

Hermes' own signals can be wrong: dispatcher health, respawn-guard holds, worker exit trailers, and the coordinator's
own "will resume". So the plugin keeps its own monitor and **trusts the board, not the messages**.

1. **Ground truth = `kanban.db` + `gh`.** A deterministic script (no LLM) reads `tasks`, `task_runs`, `task_events`,
   `task_links`, `kanban_notify_subs`, plus PR/CI state, and gives every open chain exactly one state:
   - `PROGRESSING`: a live worker (PID alive with a matching fingerprint, heartbeat recent) or a ready card claimed
     within the grace window;
   - `WAITING(<named thing>)`: a registered PR/CI watch, a per-profile cap, a quota reset time, or a human decision
     that has already been asked;
   - `STUCK(<cause>)`: anything else.
2. **Reconcile, then act.** When Hermes says one thing and the board says another, the monitor classifies the mismatch
   and runs the matching repair (clear a stale hold, reclaim a dead claim, requeue, re-subscribe). It escalates only
   what needs a person.
3. **Escalate through the wire that already works.** Chat notifications fire on card *events* (`blocked`, `completed`,
   `review_requested`, …), not on comments. So an escalation **blocks the card with a one-line reason**. That notifies
   and wakes the exact chat that started the work, on every platform including the TUI. No new delivery path, no
   separate coordinator session.
4. **Every decision is journaled.** Each classification, repair and escalation is one JSON line. Each new false signal
   becomes a named cause, a repair, and a replay test.
5. **Invariant:** every open chain is `PROGRESSING`, `WAITING(named)`, or has escalated to its chat. A chain in any
   other state is a bug, and there's a test for it.

## 3. Target numbers

| Measure | Target |
|---|---|
| Operator nudges per delivered chain | ~0 |
| Time from a chain going `STUCK` to repair or escalation | ≤ 15 min |
| Worker spawns per completed run | < 2 |
| Runs on one card | ≤ 10 |
| Blocked/triage cards older than 7 days without a decision | 0 |
| Supervisor ticks that call the LLM | < 20% |

All are computed from the journal (phase 0), so they're measured before and after each phase.

## 4. Architecture after this plan

```
                 ┌──────────── kanban.db / gh (truth) ────────────┐
                 │                                                 │
   delivery_monitor.py (cron, 5 min, no LLM)          plugin hooks (in-process)
   ├─ classify every chain → state + cause            ├─ on_kanban_dispatch_tick → holds snapshot + preflight
   ├─ run repair for known causes (mode: observe|act) ├─ pre_llm_call → nudge count, pending-escalation notice
   ├─ escalate = block card w/ reason → origin chat   ├─ post_llm_call → unsupported-claim check
   ├─ write state/delivery-state.json                 └─ on_session_end → session metrics
   └─ journal every decision
                 │                                                 │
                 └────────── logs/delivery-journal.jsonl ──────────┘
                                   │
         `hermes software-delivery log|status`, Mission Control, weekly digest
   delivery_status tool (coordinator must call before answering status questions)
   supervisor LLM job → only for judgment causes the monitor queues
```

Existing pieces this reuses rather than replaces:

- `workflow/scripts/stall_alert.py` already reads `kanban.db` directly. It **becomes** `delivery_monitor.py`. Today it
  sends its digest to a separate bot channel, mixes new stalls with dozens of old ones, and describes holds only
  vaguely ("dispatcher stuck, at capacity, or guarded").
- `software_delivery/liveness.py` keeps writing the per-tick hold snapshot. The monitor treats it as one input, not as
  authority.
- `workflow/scripts/board_triage.py` keeps its digest, but reads classifications from the monitor instead of its own
  regexes.
- `kanban-supervisor-scan.py` loses its deterministic handlers to the monitor (phase 5).

## 5. Phases

Order: measure first, then the failures that hurt most. Each phase lists the files touched, the behavior, the tests,
and what "done" means.

### Phase 0: Journal, baseline, replay harness

**Why:** today every investigation needs hand-written SQL against the board plus worker logs. The fixes can't be judged
without a before/after.

Changes:

- `workflow/scripts/delivery_journal.py` (new, installed next to the other scripts; the plugin loads it the same way
  it loads `buildcmds.py`):
  - `record(kind: str, card: str | None = None, **detail) -> None` appends
    `{"ts", "kind", "card", "chain", "actor", ...detail}` to `~/.hermes/logs/delivery-journal.jsonl`.
  - Best-effort: never raises. At 20 MB it rotates to `.1` (one generation).
  - `read(since=None, card=None, kind_prefix=None) -> Iterator[dict]`.
- Kinds (stable names, documented in the module docstring):
  - `monitor.state` (only when a chain's state changes), `monitor.repair`, `monitor.escalate`, `monitor.would_repair`
    (observe mode);
  - `hold.start`, `hold.end`;
  - `triage.retry`, `supervisor.wake`;
  - `card.outcome` (completed/blocked/gave_up with run count and duration);
  - `waste.fast_fail`, `waste.identical_failure`, `waste.duplicate_card`, `waste.quota_respawn`;
  - `nudge` (operator message that is only "continue / any update / is it done / where are we");
  - `claim.unsupported` (see phase 1);
  - `session.end` (tokens, card id).
- `software_delivery/__init__.py`:
  - the `pre_llm_call` hook counts nudges (small regex over the user message; no LLM);
  - `on_session_end` writes `session.end` through the journal. `delivery-metrics.jsonl` is kept one release for
    compatibility, then removed.
- CLI: `hermes software-delivery log [--card ID] [--chain ID] [--since 2h|3d] [--kind PREFIX] [--json]` prints a
  readable timeline.
- `workflow/scripts/delivery_baseline.py` (new): rebuilds the target numbers in section 3 from the last N days of
  `task_runs`, `task_events` and session data, prints a table, and records a `baseline` line. Run once before phase 1,
  then weekly from the digest.
- **Replay harness**:
  - `tests/fixtures/incidents/<name>/board.sql` holds a minimal anonymized board slice (tasks, runs, events, links,
    subs) and optional `worker.log`.
  - `expected.json` holds the expected state, cause and repair per card.
  - `tests/test_incident_replay.py` loads each fixture into a temp `kanban.db` and runs the classifier.
  - Seed fixtures (anonymized):
    1. review card held by a stale quota guard;
    2. retry loop on an identical error;
    3. review lane stalled by quota respawns;
    4. rework loop ending in triage;
    5. wrong base branch;
    6. worker waiting on an approval.
- **Invariant test** `tests/test_chain_invariant.py`: for every fixture, after simulated ticks each open chain is
  `PROGRESSING`, `WAITING(named)` or escalated.
- **Hygiene:**
  - land or drop the pending local changes;
  - Contributing section uses `uv run --no-project --with pytest --with pyyaml python -m pytest -q` so a stale `.venv`
    can't break it;
  - `docs/plans/*.local.md` stays git-ignored for private evidence.

Done when the journal fills during normal use, `log` shows a readable timeline, the baseline table prints, and the
replay tests fail against the current code for the stuck cases (they're the targets for phases 1–4).

### Phase 1: Truth monitor, truthful status, escalation to the origin chat

**Why:** the stuck review card in fixture 1 was detected within 19 minutes. The alert went to a side channel, labeled
"dispatcher stuck, at capacity, or guarded", inside a list of 65 items. The operator's chat heard nothing for 9 hours.

Changes:

- **Rename** `stall_alert.py` → `delivery_monitor.py`. Keep the cron job id so `merge_cron_jobs.py` updates it in
  place. Run every 5 min.
- Split into testable functions:
  - `load_board(conn, now) -> Board`: one read-only snapshot. It covers open tasks, current runs, the last 50 events per
    open card, links, notify subs, and holds from `dispatch-health.json`.
  - `chains(board) -> list[Chain]`: groups cards by `task_links` into root → leaves, and picks the **root cause**
    card. A child waiting on a stuck parent is reported through the parent.
  - `classify(card, board, now) -> Verdict(state, cause, evidence, repair, escalate_after)`: a pure function, and
    what the replay tests call.
  - `act(verdict, mode)`: runs a repair or escalation, or only journals `monitor.would_repair` in observe mode.
- **Cause catalogue v1** (each has a code, evidence fields, a repair, and an escalation rule):

  | Cause | Detected from | Repair | Escalate when |
  |---|---|---|---|
  | `WORKER_ALIVE` | running, PID alive, heartbeat < 30 min | none | — |
  | `CLAIM_PENDING` | ready/review, no run, < 10 min | none | — |
  | `AT_CAP` | hold reason = per-profile cap | none | cap held > 60 min |
  | `QUOTA_WALL` | `rate_limited` events for assignee in last 30 min | phase 2 lane pause | after reset time passes with no run |
  | `STALE_GUARD` | hold = `blocker_auth`, last run not failed, error text = quota requeue | clear stale error (D3) | repair fails twice |
  | `DEAD_WORKER` | running, PID dead or fingerprint mismatch, no heartbeat | `hermes kanban reclaim` | second time on same card |
  | `ORPHAN_REVIEW` | review, no run, no hold, > 15 min | journal + nudge dispatcher via `promote`/re-request | 30 min |
  | `PARENT_STUCK` | waiting on a parent whose state is `STUCK` | none (reported via parent) | — |
  | `TRIAGE_PARKED` | status triage | none | immediately, with the loop reason |
  | `NEEDS_HUMAN` | blocked `needs_input` | none | already escalated by the block event; reminder at 24 h |
  | `UNKNOWN_STALL` | none of the above, no movement > 20 min | none | immediately, with raw evidence |

- **Escalation** = `hermes kanban block <id> --kind needs_input "<CAUSE>: <one line>; next: <what will unstick it>"`.
  - The `blocked` event notifies and wakes every subscribed chat through Hermes' existing notifier.
  - Escalating a `review` card first records the verdict state in a comment, so unblocking returns it to review.
  - One escalation per (card, cause, error signature). Repeats only update `delivery-state.json`.
- **Side-channel digest:** the bot-channel digest keeps running. It lists only **new** stalls and chain-level summaries
  (roots, not every child) and never sends the "stuck overall: N" line unless N changed.
- **`delivery_status` tool** (new, `software_delivery/status.py`):
  - input: a card or chain id, or none for "all chains from this chat";
  - output: state, cause, evidence, age, last run outcome, PR/CI, dependents waiting, and the next expected transition.
    It reads `delivery-state.json` and recomputes live if the file is over 5 min old.
  - Orchestrator skill rule: **any status answer must come from `delivery_status`.**
- **Claim check:** the `post_llm_call` hook scans coordinator replies for "pending / queued / will resume / watching /
  in progress". If `delivery_status` for the named chain doesn't show `PROGRESSING` or `WAITING(named)`, it journals
  `claim.unsupported`, and the next `pre_llm_call` adds a one-line correction to the turn.
- **External-wait watcher:**
  - the `delivery_watch` tool registers `{kind: pr|ci, ref, card, until}` in `~/.hermes/state/delivery-watches.json`;
  - the monitor polls `gh` for registered watches and puts the card in `WAITING(pr#123 checks)` while they're open;
  - on pass or fail it escalates or unblocks per the card's next step;
  - skill rule: saying "will resume after X" requires registering a watch for X.
- **Mode switch** `delivery.monitor.mode: observe | act` in the plugin config. Default `observe` for one release; the
  journal shows what it *would* have done.

Tests:

- replay fixtures 1, 3 and 4 now classify correctly;
- `classify` table tests per cause;
- escalation dedupe;
- the review → block → unblock round trip keeps the verdict;
- watch lifecycle with a stubbed `gh`.

Done when fixture 1 reaches the origin chat within one monitor tick after the grace period, and a week in `observe`
shows no false repairs.

### Phase 2: Repairs for stuck states

**Why:** detection without repair still needs a human for things a script can fix.

Changes (all in `delivery_monitor.py`, all journaled, all respecting the mode):

- **`STALE_GUARD`:** clear `tasks.last_failure_error` for that card only, inside a write transaction, only when:
  - status is ready/review;
  - there's no running claim;
  - the latest run's outcome isn't a failure;
  - the text matches the quota-requeue pattern.
  Real auth failures stay escalated. (See D3.)
- **`DEAD_WORKER`:** `hermes kanban reclaim <id>` (the CLI, never raw SQL).
- **`QUOTA_WALL`:** after 3 rate-limited respawns for one assignee in 30 min:
  - `hermes kanban schedule` that assignee's ready/review cards. The command has no wake time, so the monitor records
    the provider's reset time (or +30 min if unknown) in `delivery-state.json` and runs `hermes kanban promote` when it
    passes;
  - one digest line;
  - optional fallback reviewer (D1);
  - `delivery_submit` caps review cards per reviewer at 1 while the wall lasts.
- **`DISPATCHER_SILENT`**, redefined: the gateway process is down, **or** no tick in `dispatch-health.json` for 10+ min
  **and** no tick line in `gateway.log` in the same window. Action: escalate once. Never run `hermes kanban dispatch`,
  because a manual dispatch bypasses ESTOP and the gateway's own caps.
- **Root-cause chains:** the digest and escalations show `root (cause) → N waiting`, never one line per child.

Tests: replay fixtures 1 and 3 end `PROGRESSING` after a repair; an auth-failure fixture stays escalated; the silent
rule doesn't fire when `gateway.log` shows ticks.

Done when fixture 1 recovers with no human action, and the silent-dispatcher false-positive rate is ~0 over a week.

### Phase 3: Retries with memory

Changes:

- **Error signature:** `signature(text)` lowercases the error and strips numbers, paths, PIDs, ids and timestamps, then
  hashes it (sha1[:10]). It's computed for every `blocked`, `gave_up`, `crashed` and `timed_out` event and stored in
  `delivery-state.json` per card.
- **`IDENTICAL_FAILURE`:** the same signature before and after an unblock or retry.
  - The card stays blocked, escalates once with both attempts' evidence, and journals `waste.identical_failure`.
  - `board_triage.py` retries `crash` / `protocol violation` / `timeout` only when the signature changed.
  - The coordinator skill forbids unblocking a card whose signature hasn't changed without new input.
- **`LIFECYCLE_DESYNC`:** the worker log's current-run section contains "unknown id or not running". Never retry;
  escalate "worker lost its claim; restart the gateway / update Hermes".
- **WIP preserved:** implementer and reviewer skills, when a terminal kanban call is rejected, must comment
  `WIP_PRESERVED: <worktree>@<sha> +<n> files; verified: <commands>`. On the next run, the implementer's PRECHECK
  accepts its own card's preserved diff instead of blocking on HEAD mismatch.
- **Goal-mode judge errors:** the worker stops and blocks with `JUDGE_ERROR` instead of spending turns.
- **Run budget:** more than 10 runs on one card, or 3 runs under 60 s in a row (`waste.fast_fail`), escalates and stops
  retries.

Tests: fixture 2 escalates once and stops; a changed signature still retries; the signature is stable across PIDs,
paths and timestamps.

### Phase 4: Don't spawn doomed work

Changes:

- **Duplicates** (`submit.py`): an open card with the same issue/PR URL, or the same repo + overlapping scope paths
  + a similar title, returns the existing card id (D4). The monitor archives cards marked superseded before they spawn.
- **Preflight** (`software_delivery/preflight.py`, new; replaces `workspace_prep.py` in `pre_llm_call`):
  - checks: the repo is a git work tree; HEAD matches the card's pinned base or branch; deps are installed for the
    detected stack (`buildcmds.py`); the gate tools the card names are on PATH; the scope paths exist at base or are
    marked `new:`.
  - runs at creation (`delivery_submit`) and on `on_kanban_dispatch_tick` for newly ready cards, cached per
    (card, HEAD).
  - safe auto-fixes: bind the worktree, install deps with the lockfile command. Otherwise block with
    `ENV_MISSING: <what>` or `SCOPE_INVALID: <path>`.
  - open item: confirm whether the dispatch-tick hook runs before or after that tick's spawns. If after, preflight
    runs at promotion via the `on_kanban_task_updated` hook instead.
- **Change already present:** if the card's acceptance tests already pass at base, block with `ALREADY_DONE` evidence
  instead of spawning.
- **Scope amendments:** a worker may block with `SCOPE_AMEND: <path> because <evidence>`. One coordinator comment
  approves it and extends the allowlist.
- **No standalone review cards:** `delivery_submit` rejects a card titled or typed as a review of another card and uses
  `request-review` on the original card instead. The monitor converts any it finds.

Tests: duplicate submit returns the existing id; each preflight failure blocks with its code; a review card is
rejected; `waste.fast_fail` drops in replay.

### Phase 5: One sense layer, script-first supervisor, one dispatcher

Changes:

- The monitor owns every deterministic handler still in `kanban-supervisor-scan.py`:
  - stale-claim reclaim;
  - superseded-review archive;
  - capacity-hold unblock;
  - marker comments;
  - unsubscribed blocks (re-subscribe the origin chat from the parent's subscription).
- The supervisor LLM job becomes `monitor_script: delivery_monitor.py --judgment-queue`. It runs **only** when the
  monitor queued a judgment cause (verdict parked in a block reason, a rework loop needing a decision, batching several
  escalations into one message). The prompt shrinks from ~13k characters to under 3k.
- **Remove every manual dispatch** from the supervisor prompt, the continuity skill and the orchestrator skill. The
  gateway is the only dispatcher.
- The coordinator wake no longer starts a separate `--source tool` session per event. It either escalates on the card
  (phase 1), or, for judgment, runs once and posts the conclusion as the block reason.
- A test asserts that each supervisor rule appears exactly once in the prompt, which catches copy-paste drift.

Done when fewer than 20% of supervisor ticks call the LLM and separate coordinator sessions drop to near zero.

### Phase 6: Keep the chat small

Changes:

- Orchestrator skill slimmed to under ~8k characters of core policy. The procedures move to `references/` files loaded
  on demand, and the contradictions found in the audit are removed:
  - "the dispatcher alone decides" vs "then dispatch";
  - "progress arrives by push" when no push existed.
- Kanban notices for one card are acknowledged in one line; repeats (same card, kind and signature) get no reply.
- Past ~150 turns or 3 compactions, the coordinator offers a handoff: a fresh session seeded with the chain summary
  from `delivery_status`.

### Phase 7: Carry the operator's instructions into the work

**Why:** base branch, branch naming, branch reuse and "don't use tool X" were each said in chat, fixed once on one card,
and repeated on the next.

Changes:

- **Project profile:** `~/.hermes/delivery/projects.yaml`, keyed by the repo's remote URL (default location; see D7).

  ```yaml
  github.com/org/repo:
    base: develop                 # every card's worktree branches from origin/<base>
    branch: "team/feat/{slug}"    # naming pattern
    reuse_chain_branch: true      # later phases continue on the chain's branch
    pr: draft-early               # open a draft PR at the first pushable commit
    tools_forbid: [opencode]      # never use unless the card says so
    notes: "…"                    # free-form, copied into every brief
  ```

  - The coordinator writes or updates it the moment the operator states a rule. The skill rule is "a stated repo
    convention is saved to the profile before replying".
  - `delivery_submit` stamps the profile into every card body.
  - Preflight (phase 4) blocks a card whose branch or base doesn't match (`CONVENTION_MISMATCH`).
- **Chain branch reuse:** later phases default to the chain's existing branch and worktree. A new branch needs a stated
  reason on the card.
- **Operator directives on the chain:** "don't wait for me / finish it all" is recorded as `autonomy: full` on the
  chain root. The monitor then repairs and retries within budgets without asking, and escalates only true decisions.
- **Draft PR is not gated by review.** A draft PR opens as soon as there's a pushable commit. Review and verify gate
  only "ready for review" and merge.
- **Rework escalation:** a second request-changes with the same finding, or rework blocked by `ENV_MISSING`, escalates
  one question (accept as-is / narrow scope / install deps) instead of looping into triage. A card entering triage
  always escalates with the loop reason (`TRIAGE_PARKED`).
- **No approval prompts in workers:**
  - Worker profiles are configured so gate commands don't prompt; `config.assertions.yaml` checks it.
  - The `pre_approval_request` hook in a worker session blocks the card with `APPROVAL_NEEDED: <command>` instead of
    waiting on a prompt nobody will see.

Tests: fixture 5 (wrong base) blocks at preflight; the profile stamps briefs; draft PR creation is allowed with an open
REQUEST_CHANGES; fixture 6 (approval) blocks with `APPROVAL_NEEDED`.

### Phase 8: Drain the backlog

Changes:

- **Expiry:** a blocked/triage card with no decision for 7 days gets one "decide or it archives in 3 days" line. At
  10 days it is archived with a final-report comment (worktree path and sha kept, so it can be revived).
- **One-time cleanup:** existing old cards go through the triage digest in groups (done / superseded / retry / decide).

## 6. Rollout and safety

- Every repair ships in `observe` mode first: it journals `monitor.would_repair` and changes nothing. After a week of
  clean journal output it switches to `act`.
- Writes use the kanban CLI wherever a command exists. The one direct column write (stale guard, D3) is narrow,
  guarded by the preconditions above, and journaled.
- ESTOP is always respected: while it is set, the monitor only observes and escalates.
- Each phase adds its incident fixtures to the replay suite before the fix lands.
- After each phase: rerun `delivery_baseline.py` and compare with section 3.

## 7. Decisions

| # | Question | Default in this plan |
|---|---|---|
| D1 | Reviewer quota wall: pause the lane, or fall back to another reviewer profile? | Pause; fallback optional per roster |
| D2 | Backlog: 7 days to a notice, 10 days to archive? | Yes |
| D3 | Stale guard: write the one column directly, or only push the fix command? | Write directly, guarded and journaled |
| D4 | Duplicate card: refuse (return existing id) or create blocked with a link? | Refuse |
| D5 | Stall notices: origin chat only, or also a daily digest channel? | Origin chat for escalations, digest channel for summaries |
| D6 | Supervisor judgment: into the origin chat, or a separate session that reports back? | Escalate on the card → origin chat |
| D7 | Project profiles: in the plugin's user-state, or in each target repo? | User-state (`~/.hermes/delivery/projects.yaml`) |
