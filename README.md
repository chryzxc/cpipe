# cpipe

[![CI](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml/badge.svg)](https://github.com/chryzxc/cpipe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-8957e5)](LICENSE)
[![Hermes Agent](https://img.shields.io/badge/built%20for-Hermes%20Agent-1f6feb)](https://github.com/NousResearch/hermes-agent)

**From an idea to a reviewed pull request, with Hermes Agent.**

cpipe brings planning, implementation, testing, and review into one development workflow. Describe a change in your Hermes chat, choose its size and execution route, and let the selected profiles or coding CLI handle the work. You can also bring an existing commit to cpipe for review, fixes, or verification.

The builder keeps its conversation through implementation and corrections. Reviewers work in separate sessions, and cpipe tracks which checks apply to the latest commit. The result is a draft pull request with verification evidence—not an automatic merge or deployment.

## What it does

- **Plans larger changes.** Review the proposed approach before implementation begins.
- **Starts smaller changes directly.** Choose a shorter path for focused fixes or content updates.
- **Tests code changes.** Small and large deliveries write and run tests before implementation.
- **Reviews the result.** Separate review and QA sessions check the change and return findings to the builder. Specialist routing uses a different review profile; single-profile routing is explicitly labelled same-profile review.
- **Works with existing commits.** Start at review, verification, or shipping without rebuilding the change.
- **Keeps context between corrections.** The builder resumes its session rather than starting over.
- **Tracks progress and usage.** Inspect delivery status, time, and available token counts.

## Install

You need Python 3.11+ and a Hermes Agent installation that supports native plugins and session message injection. GitHub publishing also requires the `gh` CLI and an authorized GitHub account.

```sh
hermes plugins install chryzxc/cpipe --enable
hermes plugins doctor ~/.hermes/plugins/cpipe --ci
hermes plugins compat ~/.hermes/plugins/cpipe
hermes config set plugins.entries.cpipe.allow_gateway_injection true
```

Run installation in an interactive terminal so you can review the dependency prompt. cpipe requires `pyyaml>=6` and `agent-client-protocol>=0.9,<0.10`.

For an existing installation, add `--force`. To install a specific revision, add `--ref` followed by its full commit SHA. If you use a separate Hermes profile, run validation against that profile's plugin directory.

Configure the profiles you want to use, then check the setup and start the worker in a separate terminal:

```sh
hermes cpipe doctor
hermes cpipe run
```

After installing or upgrading, start a fresh chat with `/new` to load the plugin's model tools. Commands and hooks may reload sooner.

To disable or remove the plugin, use the Hermes plugin manager:

```sh
hermes plugins disable cpipe
hermes plugins uninstall cpipe
```

## Start a change

In your repository's Hermes chat, describe what you want:

```text
/cp-plan --size small fix the disabled button we discussed
```

Choose a size that fits the work:

| Size | Best for | Path |
|---|---|---|
| `content` | Text, documentation, markup, styles, or configuration values—not application logic | Implementation, configured checks, and focused review |
| `small` | A focused code change with a clear scope | Tests, implementation, checks, review, and applicable QA |
| `large` | Ambiguous, risky, or cross-cutting work | Planning, your approval, tests, implementation, and verification |

If you omit `--size`, cpipe uses `large`. Content changes skip the planning session, test-writing stage, and browser QA; configured checks still run.

For a large change, review the plan and continue with:

```text
/cp-build implement the plan above
```

Regular chat remains available for work outside cpipe. Its workflow starts when you use a cpipe command.

## Choose who does the work

Before a new `/cp-plan` delivery starts, clarification offers **three choices**:

| Route | Best fit | Trade-offs |
|---|---|---|
| **Specialist bots/profiles** | Larger work that benefits from separate planning, implementation, review, and QA owners | Uses each Hermes profile's instructions and tools; requires configured, available role assignments and different build/review profiles. More setup and handoffs. |
| **External coding CLI** | Focused repository work when you deliberately want an installed CLI | Uses that CLI's authentication, permissions, settings, and instructions—not Hermes profile skills. The selected backend owns the stages; its review is same-backend review. |
| **Single Hermes profile** | Bounded work or a simple end-to-end setup | The default profile, or one other configured profile you select, owns every stage. Fewer handoffs, but review is same-profile review, not independent specialist approval. |

The question includes the resolved request, explains the trade-offs, and recommends specialists for large work or one profile for small/content work. Speed and cost depend on the task and models; neither route guarantees savings.

For single-profile or external routing, select an available configured engine next. cpipe then shows the stage owners and model settings for confirmation. No delivery is queued until you confirm. A timeout, cancellation, unavailable engine, or missing specialist setup starts nothing.

The route is saved **for that delivery**, including resumed stages and approved child work. It does not rewrite global defaults or reroute existing cards. Automatic engine fallback is disabled: a selected profile cannot silently become an external CLI. Confirmation shows explicit model overrides or an observed Hermes profile default. Unresolved defaults are labelled as such; profile/CLI defaults continue to follow that backend’s settings rather than being silently overridden.

The default Hermes profile remains an option even with a saved external default. Other profiles must be configured as Hermes engines. Mixed routing is not a fourth option.

## Chat commands

cpipe is optional. Ordinary chat requests to review a PR, compare feature parity, fix code, run tests, or verify behavior are handled directly with normal tools; they do not require any `/cp-*` command. The command receipt requirement applies only to entering cpipe, not to authorizing normal assistance. Use the commands below only when you choose the structured cpipe workflow.

Commands use the current conversation and project context. You do not need to remember a delivery ID. Hermes asks for clarification when it cannot identify the intended request or repository.

| Command | What it does |
|---|---|
| `/cp-plan` | Starts a change using the selected size |
| `/cp-build` | Builds the matching plan, or asks for confirmation to start the discussed request as a small delivery |
| `/cp-review` | Reviews existing committed work without editing or publishing it |
| `/cp-fix` | Applies the discussed findings, then checks and independently verifies the correction |
| `/cp-verify` | Runs configured checks, independent review, security checks, and applicable QA without publishing |
| `/cp-ship` | Completes review and verification, addresses gaps, and prepares a draft pull request |
| `/cp-status` | Reports the matching delivery or Git/PR status without changing it |
| `/cp-stop` | Stops the identified work while preserving its files and unrelated work |

For example:

```text
/cp-review review the change I built outside cpipe
/cp-fix correct the review findings
/cp-verify verify the behavior we discussed
/cp-ship create a draft PR for this
/cp-status how are these changes doing?
```

A command without a message uses the existing conversation. Project names and delivery IDs can be supplied when useful, but are not required.

If you use `/cp-build` before a plan exists, cpipe asks whether to start a small delivery for the resolved request and project. Selecting **Start a small delivery** opens the same three-choice routing clarification for that request. After you confirm the route, it starts automatically—you do not need to repeat `/cp-plan`. Cancelling or leaving the question unanswered starts nothing. To review work you have already implemented, use `/cp-review`.

When a large delivery has finished planning and has no unanswered questions, Hermes presents its native approval dialog with the frozen plan and confirmed execution route. Accept resumes the existing build workflow (including a draft PR if enabled); reject, dismissal, timeout, or a changed plan starts no build. Approval is for this plan only—not for PR readiness, merging, or deployment. Outstanding questions still use native clarification first. `/cp-build` remains the explicit fallback when the host has no interactive approval channel.

Commands queue a new turn in your Hermes session. The first reply confirms that the request was queued, not that the work is finished. If the host cannot queue the turn, no workflow starts. cpipe does not select an unrelated delivery as a fallback.

This interaction applies across the command family, not just `/cp-plan`: new `/cp-review`, `/cp-fix`, `/cp-verify`, and `/cp-ship` deliveries use the same routing picker. Required delivery questions and next-action choices use native clarification; completed plans, draft publication, and PR readiness use native approval dialogs bound to the current session and exact delivery revision. Selecting an answer continues in the same tool loop—you do not need to type “proceed.” Cancellation, rejection, unavailable UI transport, and stale approvals do not advance work. These approvals do not authorize merging or deployment.

Starting or resuming a delivery checks the runner, starts it if necessary, and immediately nudges queued work instead of waiting for its next scheduled scan. The card is released only after its session wake subscription is verified. Startup and dispatch failures are reported rather than presented as successful progress. Cards display the selected Hermes profile or CLI name as their assignee; internal ownership comes from delivery metadata, not a `cp:` assignee prefix.

## Bring existing work

You can enter directly at review, verification, or shipping. The source checkout must be clean and committed, with a known base branch and scope.

cpipe copies that commit onto a separate delivery branch. It leaves the source checkout in place: no stash, reset, or branch switch. Later source commits are not copied automatically; bring the new revision into cpipe when you want it reviewed.

A typical sequence is:

```text
/cp-review review this committed change
/cp-fix address the findings
/cp-verify check the corrected result
/cp-ship prepare the pull request
```

Tests added to existing work may already pass. cpipe records them as characterization or regression coverage, not as proof that the original work used test-first development.

## Review and shipping

`/cp-ship` does more than open a pull request. It reviews correctness, security, test coverage, and worthwhile refactoring within the change's scope. The builder addresses findings and missing tests, runs focused checks and the applicable regression suite, and sends corrections for independent re-review.

Approvals belong to a specific commit, base, scope, and check configuration. cpipe reuses valid results and reruns affected checks when those inputs change. After corrections, the reviewer examines the new diff and confirms that earlier findings were resolved.

Required checks that cannot run are not counted as passes. Missing QA, unresolved findings, and pending, cancelled, or skipped required CI prevent shipping from completing. An empty check configuration is reported as unconfigured, not as a successful test run.

Pull requests are drafts by default. Ask explicitly to mark one ready; that requires review, verification, and CI for the current commit. cpipe never merges or deploys.

## Configure profiles

Settings live in `~/.hermes/cpipe.yaml`. Every key is optional. Without custom settings, the default Hermes profile is available for the entire flow. Saved defaults do not replace the routing confirmation for new `/cp-plan` deliveries.

```yaml
command_only: true

default_engine: builder
roles:
  plan: planner
  build: builder
  review: reviewer
  qa: verifier

engines:
  planner:  {kind: hermes, profile: example-planner, concurrency: 1}
  builder:  {kind: hermes, profile: example-builder, concurrency: 3}
  reviewer: {kind: hermes, profile: example-reviewer, concurrency: 1}
  verifier: {kind: hermes, profile: example-verifier, concurrency: 1}

limits:
  tests: 2
  checks: 3
  qa: 2
  review: 2
  ci: 2
  round_minutes: 90

projects_root: ~/Projects
env_pass: [GH_TOKEN]
```

Replace the `example-*` names with profiles installed on your machine. Each Hermes profile uses its configured model and provider unless you set a `model` override. Profiles are not bundled with cpipe.

The builder keeps one session for tests, implementation, and fixes. Planning, independent review, and QA use separate sessions. An unavailable configured reviewer or QA profile cannot silently approve the work.

Concurrency limits apply to deliveries in each engine's lane, not to individual review or QA calls inside a delivery. `env_pass` names additional environment variables made available to workers; keep their values outside the configuration file.

### Discovery profile

For larger changes, you can add a low-cost profile that produces a short guide to the relevant source before planning or a fresh implementation session:

```yaml
roles:
  discover: explorer
  # Keep your other role assignments here.
engines:
  explorer: {kind: hermes, profile: example-explorer, concurrency: 1}
  # Keep your other engines here.
```

Discovery uses limited source excerpts and existing graph context. Its isolated Hermes session has no tools: it cannot edit files, run commands, or delegate. The result points to entry points, dependencies, tests, and open questions. The planner and builder still confirm important details against source.

Small and content changes skip discovery. Remove `roles.discover` to disable it. Only Hermes engines support this role.

Briefs are cached in Git-excluded delivery state. Changes to source, revision, request, engine settings, or graph context invalidate the cache. Calls have a 60-second run budget and a 75-second subprocess cutoff. If discovery fails or is unavailable, the workflow continues with normal source inspection; it does not retry with the builder's model or add an approval stage.

Discovery usage appears in reports. Whether it reduces overall cost or elapsed time depends on the task and needs comparative measurement.

### Other engines

Hermes is the default runner; no external coding CLI is required. cpipe also includes adapters for Codex CLI, Claude Code, and opencode.

| Engine kind | Session continuation | Result format |
|---|---|---|
| `hermes` | `hermes chat --resume` | JSON block in the final message |
| `codex` | `codex exec resume` | `--output-schema` |
| `claude` | `claude -p --resume` | `--json-schema` |
| `opencode` | `opencode run --session` | JSON block in the final message |

An engine's `kind` defaults to its name. Multiple named engines can use the same adapter. `fallback_engine` remains supported for legacy or direct backend deliveries; newly confirmed chat routes disable automatic fallback so your selection cannot silently change.

## Configure projects and checks

Project settings live in `~/.hermes/delivery/projects.yaml`:

```yaml
projects:
  my-app:
    repo: ~/code/my-app
    base_branch: develop
    engine: builder
    conventions: "Run npm test before committing."
    qa: browser  # browser | computer-use | off
    test_env:
      services:
        web: "npm run dev"
```

For a new project name, Hermes asks for the repository path. Answer with a Git checkout, or `new` to create a repository under `projects_root`. The saved project can then be referenced by name. A repository without a remote keeps its work local, with no push or pull request.

Define checks in `~/.hermes/delivery/quality.yaml`:

```yaml
checks:
  - name: lint
    files: "*.ts *.vue"
    run: "npx eslint --max-warnings=0 {files}"
    projects: [my-app]
  - name: secrets
    run: "gitleaks git --log-opts={base}..HEAD"
    stage: security
```

Checks run in parallel where applicable. They are required by default. Setting `required: false` allows an unavailable tool to be reported as a limitation; an actual check failure still needs correction.

You can provide project guidance in `~/.hermes/delivery/lessons.yaml`:

```yaml
my-app:
  - text: "Run prisma generate before the tests."
```

The ten most recent entries are included in the build prompt. Use `"*"` for guidance shared by projects. Repository conventions belong in `AGENTS.md`, and specialist procedures belong in the selected profile's skills.

## Follow progress

```sh
hermes cpipe status   # current deliveries
hermes cpipe report   # time, runs, and available token usage
```

The initiating chat receives updates when a plan is ready, the change is ready to test, a decision is needed, or the delivery completes. Reports separate per-role usage from elapsed time, so parallel review and QA are not counted twice as wall-clock duration.

## How it works

cpipe stores each delivery on a Hermes Kanban card. A separate daemon runs the workflow, while code-defined transitions determine the next step.

```text
large:   plan → approval → tests ─┐
small:                    tests ─┤
content:                        ─┤
                                 ▼
                 build ⇄ checks and corrections
                                 ↓
                 independent review · QA · security
                                 ↓
                       pull request → CI
```

A run proceeds until completion or a point that needs your input. Feedback resumes the appropriate session. Large plans with independent parts can be split into linked deliveries; dependent parts wait for their prerequisites.

### Correction limits

Corrections return new evidence to the existing builder session. Limits prevent repeated failures from continuing indefinitely:

| Correction loop | Default limit |
|---|---|
| Test-first validation | 2 |
| Build checks, including security corrections | 3 |
| Runtime QA | 2 |
| Independent review | 2 |
| Remote CI | 2 |

At the limit, cpipe pauses for a decision rather than treating unresolved findings as approval. Each run also has a time budget, configured with `round_minutes`. Human feedback can start further rounds.

### Code relationships

For code changes, cpipe can query an existing Graphify graph or CodeGraph index. Builders and reviewers receive limited context about callers, dependencies, and tests. Queries are local, read-only, and cached; they do not require another model call.

Graph results are navigation aids, not verification evidence. Profiles must confirm relationships against source. Missing tools, failed queries, and timeouts fall back to source inspection. Content changes do not query code graphs.

### Learning from verified corrections

cpipe records predefined failure patterns and verified recoveries in a local SQLite database at `$HERMES_HOME/state/cpipe-learning.sqlite`. Related worktrees share repository-specific guidance; unrelated repositories do not.

The store contains pattern keys and counts—not prompts, source code, raw findings, credentials, or transcripts. A recovery becomes guidance only after independent verification of the clean final commit. CI recovery also requires passing remote CI for that commit.

Up to five lessons are supplied to later builds. Repeated recoveries can suggest a follow-up such as a test or preflight check, but do not automatically change code, skills, or policy. Store failures do not bypass checks or stop otherwise valid work.

## Permissions and limitations

- Workers can read and modify files and run commands in their delivery worktrees. Configure profiles and tools accordingly; this is not a sandbox.
- GitHub operations use your existing `gh` authorization. cpipe's code handles pushes and pull requests. Without `gh`, a branch may be pushed without opening a PR.
- Before publishing, cpipe scans commit messages and added lines for local paths and internal identifiers, and redacts PR text. These safeguards do not replace your own secret review.
- Computer-use QA runs one delivery at a time.
- Native chat commands require session injection support and the `allow_gateway_injection` permission. Failed queuing does not start work.
- `command_only: true` keeps the chat commands as the primary interface. Setting it to `false` also exposes legacy `cpipe_plan`, `cpipe_build`, `cpipe_ship`, `cpipe_status`, and `cpipe_stop` tools.
- Command authorization records are short-lived and prevent duplicate actions. They do not restrict direct shell or Python access.
- cpipe does not merge pull requests, deploy applications, or manage credential rotation.

## Development

The current package version is **0.9.3**. The workflow is defined in `cpipe/graph.py`, with tests for transitions and correction limits. The previous implementation is preserved under the `v1-final` tag.

| Module | Responsibility |
|---|---|
| `graph.py` | Workflow transitions and limits |
| `round.py` | Executes workflow stages |
| `engines/` | Agent adapters and session continuation |
| `discovery.py`, `code_context.py` | Source navigation and code relationships |
| `learning.py` | Repository-specific verified outcomes |
| `intents.py`, `native.py`, `tools.py` | Chat commands, authorization, and Hermes integration |
| `quality.py`, `test_env.py` | Checks and application QA setup |
| `workspace_prep.py` | Worktree and dependency preparation |
| `pr.py` | Publishing, CI, and public-text checks |
| `report.py`, `settings.py` | Reporting and configuration |
| `runtime/` | Worker scheduling and lifecycle |

### Run tests

The full suite needs a Hermes Agent checkout available on Python's import path, including `hermes_cli`. Use the Hermes environment's Python to create the test environment, install the dependencies, and make that checkout importable—for example, with a `.pth` file.

```sh
uv venv --python <hermes-agent-venv-python> .venv
uv pip install --python .venv/bin/python pytest pyyaml "agent-client-protocol>=0.9,<0.10"
.venv/bin/python -m pytest -q
hermes plugins doctor . --ci
hermes plugins compat .
```

Tests use generated repositories, temporary Hermes state, and scripted agent/GitHub responses. They do not use your normal Hermes board. A standalone Python environment without Hermes cannot collect the entire suite.

The CI badge reports hosted checks. Local tests and plugin validation are separate results.

## License

[MIT](LICENSE) © 2026 Christian Rey Villablanca
