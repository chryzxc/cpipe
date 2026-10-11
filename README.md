# cpipe

cpipe is an opt-in Hermes plugin that takes a development task from request to reviewed, locally verified code on its own.

You describe the task once. cpipe investigates, shows you an approach, and asks once for approval. It then works in the background: it writes a failing test, implements, runs your project's own tests and has a second Hermes profile review the change until it is clean. When it is done, or genuinely needs you, it sends one message to the chat you started from.

It is built to be cheap with tokens:
- Every pass/fail decision is made by code from real test results, not by a model.
- The builder session is resumed and only sees what changed.
- Re-reviews look only at the latest fixes.

cpipe never commits, pushes, opens PRs, merges or deploys. You review the result in the worktree.

## How it works

```
/cp-run <task>
  └─ coordinator (your chat): investigate read-only, ask at most one batched question
       └─ approach preview (Markdown) ── your comments → new revision
            └─ freeze + native approval  ← the only interrupt
                 └─ background run ───────────────────────────────────────────┐
                      write_tests → tests must FAIL (RED)                     │
                      implement ⇄ checks (your test command, real JUnit)      │ self-repair:
                      review ⇄ implement   (until no open findings)           │ missing/misnamed/crashing
                      locally_ready                                           │ tests, failing checks,
                 └─ one chat message: result, checks, tokens, worktree ◄──────┘ crashed steps
```

1. **Clarify once.** The coordinator reads the project instructions and only the code the task touches. It asks a single batched question only for things tools cannot resolve; anything else becomes a stated assumption in the approach.
2. **Approach preview.** The coordinator produces a Markdown document covering scope, non-goals, steps, decisions, risks, checks and limits. You reply with comments or changes; each produces a new immutable revision. Saying *proceed* freezes it.
3. **One approval.** Hermes' native approval dialog shows the frozen scope. Nothing runs before you accept, and the time budget starts at approval.
4. **Background run.** The chat is free immediately. The run uses native Kanban cards titled with the working profile:
   - **write_tests:** the builder writes the behavior test. cpipe must observe it **fail on an assertion** before any implementation. A missing, misnamed or crashing test goes back to the builder with the exact reason, up to 3 rounds.
   - **implement / checks:** the builder fixes the code and cpipe runs the approved test command. Failures and broken runs go back to the builder.
   - **review:** a *different* profile reviews the full change against these axes:
     - goal, correctness, regressions (including callers outside the diff), integration, breaking behavior;
     - maintainability, security, accessibility, operability, evidence and simplicity.

     Findings carry the concrete fix. The builder fixes them all in one pass, the checks rerun, and the reviewer looks **only at the fix diff** to confirm resolution and catch regressions. Already-reviewed code is not reopened unless something high or critical turns up. This repeats until there are no open findings: at most 5 rounds, and it stops early if a round changes nothing.
5. **Notification.** You get one message in the originating chat when the run is:
   - **done:** `locally_ready`;
   - **stuck:** `diagnosis`;
   - **waiting on you**, e.g. the test command is not installed;
   - **out of budget.**

   The message includes the check results, token usage and the worktree to review. A crashed step is retried twice before reporting.

## Builder and reviewer

cpipe uses two of your saved Hermes profiles: a **builder** and a different **reviewer**. Models and providers always come from the profiles; cpipe never guesses them.

- **First run:** if no roles are saved, the coordinator asks which profiles to use, in its single question. It offers only profiles that have a saved model and provider.
- **After that:** the choice is remembered in `cpipe-deliveries/roles.json` and reused without asking.
- **Removed profile:** if a remembered profile is removed or loses its model, cpipe asks again.
- **Too few profiles:** with fewer than two usable profiles, `/cp-run` stops immediately and explains why.

Use your strongest model as the reviewer: a reviewer that finds everything in round one keeps the loop short. The builder can be a cheaper model.

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
| `builder`, `reviewer` | Distinct saved Hermes profiles (see above). |
| `seconds` | Execution budget, 30–7200 (3600 suggested for features). Up to 24 builder/reviewer turns. |
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
- **Runs inside Hermes.** A background run lives in the Hermes process. If Hermes restarts mid-run, the next build of that delivery recovers the abandoned step.
- **No publication.** There is no publication adapter. `locally_ready` means verified in the worktree, not pushed.

## Diagnostics and development

```sh
hermes cpipe doctor
hermes cpipe status
hermes cpipe smoke
# Opt-in: runs live models and consumes usage.
hermes cpipe live-probe --builder <profile> --reviewer <profile> --seconds 480
```

Tests need the supported Hermes Agent checkout (set `HERMES_AGENT_ROOT` if it is not in the default location). They use throwaway Hermes homes and boards, never your live board.

```sh
python -m pytest tests
hermes plugins doctor . --ci
hermes plugins validate . --json
```
