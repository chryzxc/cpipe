# cpipe

cpipe is a Hermes plugin that delivers a development task from request to reviewed, locally verified code.

You describe the task once. cpipe investigates, presents an approach and asks for a single approval. The delivery then runs in the background: a failing test is written first, the change is implemented, the project's own tests are run, and a second Hermes profile reviews the result. When the delivery finishes, or needs a decision from you, cpipe sends one message to the originating chat.

cpipe does not commit, push, open pull requests, merge or deploy. The result is left in the worktree for your review.

## Design

### Contract first

- **Clarify once:** open questions are batched into one round before planning; anything else becomes a stated assumption.
- **Frozen contract:** the approved approach (goal, acceptance, scope, checks, profiles, budget) is immutable and identified by a content digest. Every later step is bound to it.
- **Bound consent:** approval is tied to the plan digest, the worktree fingerprint and the chat session. No model-provided flag counts as approval.

### Graph engineering

A delivery is a state graph driven by code, not by a model.
- **Nodes** are activities: `write_tests`, `implement`, `checks`, `review`, plus the end states `locally_ready`, `waiting`, `diagnosis` and `stopped`.
- **Edges** are chosen by a deterministic reducer from observed facts: test results, review verdicts, step failures and budget. A model never decides which step comes next or whether the work passed.

### Event sourcing and durability

- **Journal:** every fact is appended to a local transactional store. State is the projection of those facts, so a delivery resumes from its last fact after any interruption.
- **Optimistic concurrency:** each event carries the version it expects; a stale writer is rejected.
- **Idempotency:** events and intents carry keys; replaying one is a no-op, and reusing a key with different content is rejected.
- **Outbox:** chat messages are stored with the state change that produced them and redelivered until the chat accepts them.

### Evidence over claims

- **Observed results only:** pass/fail comes from running the project's own commands (JUnit per test, or exit code). Worker prose is never evidence.
- **Content fingerprints:** every check result is bound to a hash of the worktree it ran on. A result for an older tree does not count.
- **Separation of duties:** the reviewer is a different profile from the builder and works read-only.

### Test-driven delivery

- **Red first:** the behavior test must fail on an assertion before implementation starts.
- **Green by the checks:** the change is done when the approved checks pass.
- **Proven regression tests:** a behavior fix from review must include a test that fails with the fix undone.
- **Baseline:** failures that already exist on the base code are recorded once and never attributed to the change.

### Loop engineering

Each loop in the graph has an exit condition and a bound.
- **Test loop:** up to 3 rounds per builder to produce a valid failing test.
- **Fix loop:** implement and checks repeat until every check passes, with failures returned to the builder.
- **Review loop:** until no blocking findings remain, up to 5 rounds per builder, ending early when progress stops (see [Review](#review)).
- **Severity triage:** only serious in-scope findings drive the loop; low findings are reported as advice.
- **Escalation ladder:** when a loop reaches its bound, the next approved profile takes over in a fresh session. A delivery always ends in a defined state.

### Checkpoints and rollback

- **Last-good checkpoint:** after every verified step, the worktree is recorded under a private Git ref.
- **Rollback:** a failed or declined step is reset to that checkpoint, including staging, commits and branch switches.
- **Scope enforcement:** edits outside `write_paths` are rejected and rolled back.

### Fault tolerance

- **Retry with backoff:** failed steps, check runs and setup are retried with increasing delays.
- **Crash-loop parking:** a run that crashes repeatedly at the same point is parked and retried on a timer, similar to a circuit breaker.
- **Watchdog:** long-running Hermes processes resume orphaned runs, and a single owner record ensures only one process resumes each run.
- **Timeouts and budgets:** each step has a wall-clock cap, and the delivery has a time and turn budget set at approval.

### Token efficiency and caching

- **Stable prefix:** a new worker session receives the frozen contract once, in a stable form that provider prompt caches can reuse.
- **Session reuse:** builder and reviewer sessions are resumed across rounds. A resumed session receives only the delta: failing checks, open findings and the current task.
- **Diff-scoped re-reviews:** after the first full review, the reviewer inspects only the diff since its last review.
- **Code-made decisions:** verification and routing cost no model tokens.
- **Usage tracking:** input, output, cache-read and cache-write tokens are recorded per step and reported with the result.

### Safety

- **Least privilege for checks:** checks run with a minimal environment (`PATH`, `HOME`, locale, `CI=1`) and no credentials.
- **Redaction:** credentials are written as `[REDACTED]`, and review text is redacted before it is stored or shown.
- **Local only:** cpipe never commits, pushes or deploys.

## How it works

```
/cp-run <task>
  └─ coordinator (your chat): investigate read-only, ask at most one batched question
       └─ approach preview (Markdown) ── your comments → new revision
            └─ freeze + native approval  ← the only interrupt
                 └─ background run ───────────────────────────────────────────┐
                      write_tests → tests must FAIL (RED)                     │
                      implement ⇄ checks (your test command, real JUnit)      │ self-repair:
                      review ⇄ implement   (until no serious findings)        │ missing/misnamed/crashing
                      locally_ready                                           │ tests, failing checks,
                 └─ one chat message: result, checks, tokens, worktree ◄──────┘ crashed steps
```

1. **Clarify once.** The coordinator reads the project instructions and only the code the task touches. It asks a single batched question only for things tools cannot resolve; anything else becomes a stated assumption in the approach.
2. **Approach preview.** The coordinator produces a Markdown document covering scope, non-goals, steps, decisions, risks, checks and limits. You reply with comments or changes; each produces a new immutable revision. Saying *proceed* freezes it.
3. **One approval.** Hermes' native approval dialog shows the frozen scope. Nothing runs before approval, and the time budget starts at approval.
4. **Background run.** The chat remains available. The run uses native Kanban cards titled with the working profile:
   - **write_tests:** the builder writes the behavior test. cpipe must observe it **fail on an assertion** before any implementation. A missing, misnamed or crashing test goes back to the builder with the exact reason, up to 3 rounds.
   - **implement / checks:** the builder fixes the code and cpipe runs the approved test command. Failures and broken runs go back to the builder.
   - **review:** a *different* profile reviews the full change against these axes:
     - goal, correctness, regressions (including callers outside the diff), integration, breaking behavior;
     - maintainability, security, accessibility, operability, evidence and simplicity.

     Each finding includes the concrete fix. The builder addresses all findings in one pass, the checks run again, and the reviewer inspects the fix diff.

     See [Review](#review) for how findings are handled.
5. **Notification.** You get one message in the originating chat when the run is:
   - **done:** `locally_ready`, with any open findings and advice listed;
   - **stuck:** `diagnosis`;
   - **waiting on you**, e.g. the test command is not installed;
   - **out of budget.**

   The message includes the check results, token usage, the worktree to review and every recovery cpipe made on its own (see below).

## Review

- **Blocking findings:** in-scope critical, high and medium findings go back to the builder. Low findings are listed in the final message as advice.
- **Re-reviews:** a new finding is medium or above only if the latest fixes caused it, and it names the file, the line and the failure.
- **Fixes:** the builder changes only the code a finding is about, and the checks rerun after every fix.
- **Regression tests:** a behavior fix comes with a regression test in a file the checks run. cpipe runs the checks with the fix undone; the test must fail there, otherwise the step is rolled back and retried.
- **Disputes:** the builder may dispute a finding with a reason instead of changing code. The reviewer accepts it or keeps the finding open.
- **Limits:** fixing stops when the blocking findings stay the same, when their count does not drop for 2 rounds, or after 5 rounds. The next approved builder then takes over. If none resolves them and the checks pass, the run ends `locally_ready` with the open findings and disputes listed.

## Self-recovery

A delivery stops for you only when no approved action can move it forward. Other failures are handled in the background:

- **A step fails** (malformed or missing JSON, no edits, edits outside `write_paths`, a commit, branch switch or staged files, a crash, provider error or timeout): the step's changes, staging, commits and branch switch are rolled back to the last verified worktree. The same profile then retries with the reason, first in its session and then in a fresh one, with a short backoff. After 3 failures the next approved profile for that role takes over.
- **A profile declines** (`{"status":"blocked"}`): rolled back the same way, and the next approved profile takes over at once.
- **A loop is stuck** (no valid failing test after 3 rounds, or review findings that stop converging): the next approved builder gets the same rounds in a fresh session. After the last builder, a test loop reports `diagnosis`. A review loop with passing checks ends `locally_ready` with the open findings listed.
- **A worker hangs:** each step is capped at a third of the budget (at least 10 minutes).
- **Leftover edits with nothing to roll back to** (e.g. a crash before cpipe had verified anything): if they stay inside `write_paths`, cpipe continues from them and runs the checks again before any review.
- **The repository already fails a check** (for example an unrelated failing test): on the first check run, cpipe runs every check on the base code (its own edits put aside and restored, even after a crash) and remembers what already fails. A check that later fails only on those counts as passed and says so in the report; any new failure still goes back to the builder. The behavior test never gets this pass.
- **A check run or step setup crashes:** retried with backoff; the run reports only after 3 failures in a row.
- **The run itself crashes:** it restarts in the background as long as it keeps making progress. After repeated crashes at the same point it is parked and retried every 10 minutes until it works or its budget ends; the chat is told once.
- **Hermes restarts or crashes:** a watchdog in every long-running Hermes process (and the next chat turn) resumes the run from its last step. Exactly one process resumes it.
- **Something else takes cpipe's cards** (a board automation, an older background service): a card that was reassigned, claimed by something else or left behind is archived and replaced. Cards are claimed the moment they are queued, and released cards are archived so nothing picks them up again. `hermes cpipe doctor` warns when this happens.
- **The budget runs out while no step is running:** the next resume records the stop and reports it.
- **"Continue" in the chat:** asking the coordinator to continue calls build again, which restarts an idle run and returns its current state. This applies to a run that stopped, is waiting, reported `diagnosis` or finished with open findings:
  - it picks up from the code as it is now, so fix the cause first if it was yours (e.g. install the test command);
  - every approved builder and reviewer gets its full rounds again;
  - if the time or turn budget ran out, the same native approval asks you for a fresh budget, and the run continues after you accept.

### Monitoring

- **Progress line in the desktop app:** a single line above the chat input shows what the run is doing (`forge is implementing`, `running the checks`, `sentry is reviewing`) and every handover or recovery as it happens. It is display only: no model turn, no tokens. Enable **cpipe** under **Capabilities → Plugins** (use **Rescan** after installing or updating). Runs hosted outside the desktop app (gateway, terminal chat) skip it.

- Every message to the chat is stored until the chat accepts it; the watchdog and the next chat turn redeliver it.
- `/cp-status` shows whether anything is actually working on a delivery (`background`), when a parked run retries and why, and any message not yet delivered.
- Starts, crashes, parking, resumes and deliveries are logged to the Hermes agent log under `cpipe`.
- If a run ends within seconds of approval, the build reply reports the result directly.
- One-shot queries (`-z`, `-q`, `--query-file`, `--oneshot`) and kanban workers never host or resume runs. Only one run works in a worktree at a time.

A delivery stops and waits for you when (say "continue" once resolved):
- the approved time or turn budget runs out (you set it; continuing asks for a fresh one);
- every approved builder or reviewer declined or kept failing;
- the test command is not installed (an environment problem cpipe should not fix);
- files outside `write_paths` were changed and cpipe cannot restore them (it never deletes work it cannot attribute).

Only a cancelled delivery or a changed plan document cannot continue.

`/cp-status` always shows the result, even before the message arrives.

## Builder and reviewer

cpipe uses your saved Hermes profiles: one or more **builders** and one or more different **reviewers**, each list in order. Models and providers always come from the profiles; cpipe never guesses them.

- **First run:** if no roles are saved, the coordinator asks which profiles to use, in its single question. It offers only profiles that have a saved model and provider, each with the role line from its `SOUL.md`.
- **Read-only profiles never build:** a profile whose `SOUL.md` declares it read-only is marked review-only in the offer and rejected as builder before approval.
- **Fallbacks:** `builder` and `reviewer` can each be an ordered list, shown in the approach and covered by the one approval. The next one takes over when the current one declines or keeps failing (see [Self-recovery](#self-recovery)). Read-only profiles make good fallback reviewers.
- **After that:** the choice is remembered in `cpipe-deliveries/roles.json` and reused without asking.
- **Removed or unusable profile:** a remembered profile that was removed, lost its model or turned read-only as builder is skipped; cpipe asks again only if a role is left empty.
- **Too few profiles:** with fewer than two usable profiles, `/cp-run` stops immediately and explains why.

A strong reviewer model keeps the review loop short. The builder can use a cheaper model.

## Install or update

Private repositories need an already-authorized Git/GitHub account. Never put credentials in an install URL, chat, plan or artifact.

```sh
hermes plugins install chryzxc/cpipe --force --ref <commit> --enable --yes-deps
hermes plugins doctor cpipe --ci
hermes cpipe doctor
```

Start a new Hermes chat after updating; a running chat keeps the old tools loaded.

## Commands

- `/cp-run <task>`: the only entry point. One session-bound receipt carries investigation, revisions, approval and the run. You never copy IDs or type a second command.
- `/cp-status [delivery_id]`: current phase, each check's result (with the reason when a check produced no result), review rounds and tokens.
- `/cp-stop <delivery_id>`: cancel a delivery.

Ordinary chat requests never start cpipe. The model sees exactly two tools:
- `cpipe_execute_intent`;
- `cpipe_approve_plan`. Approval binds to the frozen plan, session and profile; no model-provided flag counts as consent.

## The execution spec

The coordinator fills in the spec; it is shown in the preview and frozen at approval.

| Field | Meaning |
|---|---|
| `worktree`, `repo`, `base` | Where the work happens. A dedicated worktree is recommended. |
| `goal`, `acceptance`, `non_goals` | What done means. |
| `write_paths`, `forbidden_paths` | Edits outside `write_paths` are rejected. |
| `builder`, `reviewer` | Distinct saved Hermes profiles; each may be an ordered list of up to 3 (see above). |
| `seconds` | Execution budget, 30–7200 (3600 suggested for features). Up to 40 builder/reviewer turns. |
| `checks` | The repository's own test commands (see below). |

Each check has an `obligation`, an `argv`, `criteria` and a `timeout_seconds`. Exactly one check has the obligation `behavior`: it is the test proving the change, and its file must be inside `write_paths`.

- **`argv`:** the command, run in the worktree. Its program must be on `PATH` or given as an absolute path.
- **`criteria`:** the test names.
- **With `{report}` (preferred):** if `argv` contains `{report}`, cpipe swaps in a private JUnit path and reads per-test results. A criterion matches:
  - `name`;
  - `classname::name`;
  - the title under describe blocks (vitest's `describe > title`, jest's `describe title`).
- **Without `{report}`:** the exit code decides, and every criterion must appear in the output. That way a build error is never mistaken for a failing test.

```text
["uv", "run", "pytest", "tests/test_x.py", "--junitxml={report}"]
["npx", "vitest", "run", "src/x.test.ts", "--reporter=junit", "--outputFile={report}"]
["go", "test", "./...", "-run", "TestX", "-v"]
```

Prefer commands that provision their own dependencies; otherwise install first (`sh -c "npm ci && ..."`). Checks run with a minimal environment (`PATH`, `HOME`, locale, `CI=1`) and no credentials.

## Security and limits

- **Not sandboxed.** Builders, reviewers and checks are trusted local processes. Scope and artifact checks detect violations; they do not prevent file or network access.
- **Dispatcher unchanged.** cpipe's cards are assigned to `cp:<profile>`, so the normal Hermes dispatcher does not pick them up. The dispatcher itself is not modified.
- **No credentials kept.** Credentials must be written as `[REDACTED]`. Worker prose is never treated as evidence; only observed test results and validated review verdicts are.
- **Runs inside Hermes.** A background run lives in a long-running Hermes process (the app, TUI or gateway). If Hermes restarts mid-run, the watchdog or the next chat resumes it; while no Hermes process is running, nothing runs, but the budget clock keeps going.
- **Shared provider limits.** All builders and reviewers on one provider share its rate limits; a fallback on another provider makes the ladder more robust.
- **No publication.** There is no publication adapter. `locally_ready` means verified in the worktree, not pushed.

## Diagnostics and development

```sh
hermes cpipe doctor
hermes cpipe status
hermes cpipe smoke
# Runs live models and consumes usage.
hermes cpipe live-probe --builder <profile> --reviewer <profile> --seconds 480
```

Tests need the supported Hermes Agent checkout (set `HERMES_AGENT_ROOT` if it is not in the default location). They use throwaway Hermes homes and boards, never your live board.

```sh
python -m pytest tests
hermes plugins doctor . --ci
hermes plugins validate . --json
```
