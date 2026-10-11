# cpipe

A standalone, opt-in Hermes plugin for **local-only** development. Start with `/cp-run <task>`: investigate or design, review a Markdown approach, explicitly approve the frozen scope, then implement and verify. No Hermes Agent fork or core dispatcher modification is required.

## Install or update

Private repositories require an already-authorized Git/GitHub account on the machine. Never put credentials in an install URL, chat, plan or artifact.

```sh
hermes plugins install chryzxc/cpipe --force --ref <exact-40-character-commit> --enable --yes-deps
hermes plugins doctor cpipe --ci
hermes cpipe doctor
```

An exact revision is intentionally pinned. Reinstall with an explicit newer revision to update it. Restart the Hermes application/session after installation: an already-running process can retain old imported tools. The installed manifest, package and Python project all report version `2.0.0`.

## Explicit entry points

Ordinary chat requests use normal Hermes tools; they do not silently start cpipe. Supported commands:

- `/cp-run <task>`: the task-oriented entry point. One session-bound receipt carries investigation, approach revisions, explicit native approval and execution. No separate plan/build commands or copied delivery IDs are required from the user.
- `/cp-status [delivery_id]`: read the profile-local journal directly.
- `/cp-stop <delivery_id>`: revoke a delivery. An already-running unsandboxed child cannot be forcibly confined by this command; lease/revocation checks reject further evidence.

There are exactly two model tools: `cpipe_execute_intent` and `cpipe_approve_plan`. The latter accepts only `delivery_id` and `plan_revision`; no model-provided approval flag is authority. Approval is scoped to the frozen artifact, plan, session and active Hermes profile. The approval tool starts no workers and permits no publication. In `/cp-run`, the coordinator continues after explicit consent using the same receipt. Rejected, expired, wrong-session or changed envelopes cannot build.

`/cp-run` needs Hermes' native session-injection permission. If injection is unavailable it starts nothing, removes the unused receipt and reports the failure.

### Task-sensitive approach and review

1. **Resolve the task and repository.** Inspect project instructions, relevant code and saved configuration. Ask a focused question only when missing information changes scope, ownership or intended behavior. Installed relevant skills are used when available; optional skills and Desktop preview are not runtime dependencies.
2. **Investigate bugs before proposing a fix.** Reproduce safely or trace the cause, recording real evidence. For features, establish expected behavior and design. Small clear changes need a focused approach; features and high-risk or cross-cutting work need fuller design regardless of whether they are described as bugs. Investigation is read-only: do not edit application code or run unapproved project tests.
3. **Prepare the approach.** Call `cpipe_execute_intent` with the same opaque receipt, `operation: prepare` and `{spec, approach, expected_revision}`. Preparation validates a supported execution spec without proposing a delivery or starting its execution budget. The structured approach has exactly `kind` (`bug`, `feature` or `change`), `depth` (`focused` or `detailed`), `risk` (`normal` or `high`), `summary`, and the string lists `evidence`, `steps`, `decisions`, `risks`. Steps are required; a bug needs investigation evidence; detailed approaches need explicit design decisions. First preparation uses revision `0`; edits name the current revision.
4. **Present the Markdown artifact.** The document includes scope, non-goals, verification, actors, limits and permissions. Put the returned `preview_attachment` (`MEDIA:` followed by the quoted local path) on its own line in the assistant reply. Herm renders a file card with a clickable **Open preview** button. Saving a file alone does not open preview, and Herm intentionally disables ordinary `file://` Markdown links. The returned `preview_link` is a fallback for clients that support local-file links. Where preview is unavailable, review the document in an editor or chat.
5. **Consume review feedback.** Herm sends review comments into this same conversation as a user message. Integrate the feedback with `prepare`, creating a new immutable revision and a new attachment. Even when the comment prompt requests editing in place, do not overwrite the managed reviewed file. Resolve substantive comments and present the latest approach again. No automatic comment parser or separate comment UI is required.
6. **Freeze, then ask for explicit execution approval.** Freeze the latest `draft_revision` with the internal `plan` operation. The frozen plan embeds the exact approach text in its digest. Show the frozen scope through `cpipe_approve_plan`. Opening, commenting on or clicking the preview's document-review approval button does **not** authorize workers; native execution approval remains mandatory.
7. **Continue after consent.** On native acceptance, consume the internal `build` stage using the same receipt and returned delivery identity; the user need not type another command. Denial, unavailable approval transport, stale source/documents and expired authority fail closed. Repeating a completed stage returns its saved result rather than relaunching workers. An interrupted claimed stage is not proof of success and is not automatically retried.

Approaches are stored under the active profile's `cpipe-deliveries/approaches/` directory (directory mode `0700`, files `0600`). Each revision is separate. Source and document identities are checked before freezing and at the approval/execution boundary. Sensitive text is redacted before receipt persistence; structured approach/spec fields containing recognizable credentials are rejected rather than retained. The execution budget starts when the plan is frozen, not when the user begins reviewing a draft. The run receipt expires after 24 hours.

## Execution contract

A spec must explicitly provide `worktree`, `repo`, `base`, `goal`, `acceptance`, `non_goals`, `write_paths`, `forbidden_paths`, `builder`, `reviewer`, `seconds`, and `checks`. These are not optional inferred defaults. `builder` and `reviewer` are distinct saved Hermes profile names. Their model/provider values are read from the saved profiles; external CLI engines are unsupported. `seconds` is a bounded integer from 30 through 1800.

Each check is the repository's own test command, in any language: `obligation`, `argv` (run in the worktree; the program must be on `PATH` or absolute), `criteria` (test names) and `timeout_seconds`. If `argv` contains `{report}`, cpipe swaps in a private JUnit XML path and reads per-test outcomes, with each criterion matching one reported `classname::name` or `name`. Otherwise the exit code decides, and every criterion must appear in the output, so a compile or import error is not mistaken for a failing test. Exactly one check has obligation `behavior`, and it must fail before implementing. The checks and scope are frozen into the approval digest. A dedicated worktree is recommended. The worktree fingerprint includes its Git-visible files; artifacts are invalidated on mutation. Changes outside the write allowlist cannot become implementation receipts.

Examples: `["uv", "run", "pytest", "tests/test_x.py", "--junitxml={report}"]`, `["npx", "vitest", "run", "src/x.test.ts", "--reporter=junit", "--outputFile={report}"]`, `["go", "test", "./...", "-run", "TestX", "-v"]`. Prefer commands that install their own dependencies; otherwise put the install step first in the command (`sh -c "npm ci && ..."`). Checks run with a minimal environment (`PATH`, `HOME`, locale, `CI=1`) and no credentials.

The controller uses the journal, native task ownership, bounded Hermes stream workers, actual test-command observations and independent review observations. A passing readiness result is `locally_ready`, not a PR, merge or deployment. Setup repair, automatic correction loops and publication adapters are not exposed by this controller. Failures remain inspectable and fail closed.

## Security and limitations

Live builders, reviewers and checks are **trusted local, unsandboxed execution**. Scope and artifact checks detect violations; they do not prevent file or network access. Native Kanban claims coordinate launchers but provide no isolation. The normal Hermes dispatcher is unchanged; cpipe does not promise to exclude it from cpipe-owned tasks. Credentials must be replaced with `[REDACTED]`; worker prose and diagnostics are not retained as authoritative test evidence.

The offline smoke fixture's separate sandbox tests do not establish isolation for live workers. Likewise, successful fixture tests do not establish a complete production delivery for an arbitrary repository. The plugin intentionally has no PR, push, merge or deployment operation. Reviewing the approach changes no publication permission.

The adaptive planning and comment interpretation happen in the native coordinator session, guided by the injected task workflow and available skills. The plugin enforces draft revisions, session binding, frozen-plan consent and execution boundaries; it cannot prove that a model's claimed investigation or design judgment is correct.

## Diagnostics and verification

```sh
hermes cpipe doctor
hermes cpipe status
hermes cpipe smoke
# Explicit opt-in only: runs live models and consumes usage.
hermes cpipe live-probe --builder forge --reviewer sentry --seconds 480
```

`smoke` uses disposable fixture state and fails if its sandbox boundary is unavailable. `live-probe` also uses disposable fixture state, but its workers are unsandboxed. Neither diagnostic migrates or resumes previous deliveries.

Development tests require the supported Hermes Agent checkout and pytest. Set `HERMES_AGENT_ROOT` if the checkout is not in the default local location. Tests use throwaway Hermes homes and native boards, not the operator's live board.

```sh
python -m pytest tests
hermes plugins doctor . --ci
hermes plugins validate . --json
```

The controlled `/cp-run` integration exercises native consent transport, real failing/passing checks and fixture builder/reviewer callbacks. It is not proof of a live human approval click or a paid-model end-to-end delivery. Herm's attachment/comment contract is verified in source; do not claim a live installed preview-and-delivery lifecycle without exercising it.

Old delivery state is not automatically imported. Back up any locally edited installed-plugin files before changing installation source.
