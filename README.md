# cpipe v2

A standalone, opt-in Hermes plugin for **local-only** development. V2 is the only supported engine. The legacy daemon, stage workflow, external coding CLI adapters, publication tools and compatibility paths have been removed. No Hermes Agent fork or core dispatcher modification is required.

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

- `/cp-plan <request>`: issue a session-bound receipt and inject the request into the current native session. The coordinator resolves an explicit local execution spec, proposes a frozen envelope, presents its scope and asks for native human approval.
- `/cp-build <delivery_id>`: execute only that same-session, approved envelope.
- `/cp-status [delivery_id]`: read the profile-local v2 journal directly.
- `/cp-stop <delivery_id>`: revoke a delivery. An already-running unsandboxed child cannot be forcibly confined by this command; lease/revocation checks reject further evidence.

There are exactly two model tools: `cpipe_execute_intent` and `cpipe_approve_plan`. The latter accepts only `delivery_id` and `plan_revision`; no model-provided approval flag is authority. Approval is scoped to the frozen artifact, plan, session and active Hermes profile. It starts no workers and permits no publication. Rejected, expired, wrong-session or changed envelopes cannot build.

`/cp-plan` needs Hermes' native session-injection permission. If injection is unavailable it starts nothing, removes the unused receipt and reports the failure. No legacy fallback exists.

## Execution contract

A plan must explicitly provide `worktree`, `repo`, `base`, `goal`, `acceptance`, `non_goals`, `write_paths`, `forbidden_paths`, `builder`, `reviewer`, `seconds`, and `checks`. These are not optional inferred defaults. `builder` and `reviewer` are distinct saved Hermes profile names. Their model/provider values are read from the saved profiles; external CLI engines are unsupported. `seconds` is a bounded integer from 30 through 1800.

Each pytest check provides `obligation`, relative Python-file `targets`, exact JUnit `classname::name` `criteria`, and `timeout_seconds`. The checks and scope are frozen into the approval digest. A dedicated worktree is recommended. The worktree fingerprint includes its Git-visible files; artifacts are invalidated on mutation. Changes outside the write allowlist cannot become implementation receipts.

The controller uses the v2 journal, native task ownership, bounded Hermes stream workers, actual pytest observations and independent review observations. A passing readiness result is `locally_ready`, not a PR, merge or deployment. Setup repair, automatic correction loops and publication adapters are not exposed by this controller. Failures remain inspectable and fail closed rather than reverting to an old pipeline.

## Security and limitations

Live builders, reviewers and pytest checks are **trusted local, unsandboxed execution**. Scope and artifact checks detect violations; they do not prevent file or network access. Native Kanban claims coordinate launchers but provide no isolation. The normal Hermes dispatcher is unchanged; cpipe does not promise to exclude it from cpipe-owned tasks. Credentials must be replaced with `[REDACTED]`; worker prose and diagnostics are not retained as authoritative test evidence.

The offline smoke fixture's separate sandbox tests do not establish isolation for live workers. Likewise, successful fixture tests do not establish a complete production delivery for an arbitrary repository. The plugin intentionally has no PR, push, merge or deployment operation.

## Diagnostics and verification

```sh
hermes cpipe doctor
hermes cpipe status
hermes cpipe v2-smoke
# Explicit opt-in only: runs live models and consumes usage.
hermes cpipe v2-live-probe --builder forge --reviewer sentry --seconds 480
```

`v2-smoke` uses disposable fixture state and fails if its sandbox boundary is unavailable. `v2-live-probe` also uses disposable fixture state, but its workers are unsandboxed. Neither diagnostic migrates or resumes legacy deliveries.

Development tests require the supported Hermes Agent checkout and pytest. Set `HERMES_AGENT_ROOT` if the checkout is not in the default local location. Tests use throwaway Hermes homes and native boards, not the operator's live board.

```sh
python -m pytest tests
hermes plugins doctor . --ci
hermes plugins validate . --json
```

Old delivery state is not automatically imported. Back up any locally edited installed-plugin files before changing installation source. Keeping a backup does not re-enable legacy code.
