# cpipe

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that delivers a coding task to reviewed, verified
code. You hand over the task; cpipe clarifies once, plans, gets one approval, writes the tests first, builds,
verifies with the project's own checks, has the change reviewed in a fresh context, cleans up, commits and
reports back.

cpipe is a set of skills. The model in your chat runs every step with its normal tools, so it adapts to each
repository: when a command is blocked, a test runner is broken or a dependency is missing, it works around it,
records what it could not verify, and keeps going. It stops only for decisions that are yours.

## Install

```sh
git clone https://github.com/chryzxc/cpipe ~/.hermes/plugins/cpipe
hermes plugins enable cpipe
```

## Use

```
/cp-run <task>
```

`/cpipe:run <task>` does the same. Say **continue** in the chat to resume a run after a restart.

## Flow

```
set up (worktree + dependencies) → explore → design → clarify once → plan → approve
   → tests first (RED) → build (GREEN, one verified change at a time) → verify
   → QA on the real surface → independent review (≤ 2 rounds) → clean up → verify → commit → report
```

Every step runs on every task. A step that does not apply (design for an internal fix, QA for a docs change)
is skipped with its reason in the run log. The checks and the report are always done by the chat itself.

Your own skills come first: cpipe sets the order of the steps, and at each step the chat loads any of your skills
that covers that work (pull-request content, commit messages, review, UI design, testing) and follows it over
cpipe's defaults. cpipe's guard rails still apply.

| Skill | Step |
|---|---|
| `cpipe:run` | The flow, the rules, the run log, playbooks per task kind |
| `cpipe:plan` | Worktree and dependencies, explore the code, the checks and their baseline, clarify once, plan, approval |
| `cpipe:design` | States, flow, copy, accessibility and edge cases of anything user-facing |
| `cpipe:build` | Tests first, implement, cleanup pass |
| `cpipe:verify` | Run the checks with honest states, work around checks that will not run, completion gate |
| `cpipe:qa` | Use the change on the real surface, walk the acceptance criteria, try to break it, broader suite |
| `cpipe:review` | Independent review by a subagent, findings sorted into act, consider, noted and dismissed |
| `cpipe:report` | Commit, a draft pull request only when asked, the report |

### Roles (optional)

By default the chat does every step itself, with a subagent as the reviewer. To hand steps to your own Hermes
profiles, create `~/.hermes/cpipe.yaml`:

```yaml
roles:
  explore: <profile>    # maps the code before planning
  design: <profile>     # designs the user-facing side
  plan: <profile>       # drafts the plan
  build: <profile>      # writes the tests and the code
  qa: <profile>         # uses the change like a user would
  review: <profile>     # reviews the change
  security: <profile>   # second reviewer for security-sensitive changes
```

Every role is optional. The chat still runs the flow, runs the checks itself and treats each profile's answer as
a claim to verify. At the start of each run it tells you who does each step; a role with no profile runs in the
chat. If a profile fails or times out, the chat retries once, tells you, and does the step itself.

### Playbooks

| Kind | Proof |
|---|---|
| bug | A test that fails before the fix and passes after it |
| feature | New tests red then green, existing checks still green, behavior exercised on the real surface |
| refactor | The same checks green before and after |
| chore | Checks green, change read back |

### When it stops

Only for: the plan approval (once), a decision only you can make (credentials, anything destructive or
irreversible, anything leaving your machine you did not ask for, a product question without a sensible default),
or an open security finding it cannot fix inside the scope. It never asks you to fix or run anything itself.

### Honest states

Every check in the report is one of **pass**, **fail**, **baseline** (already failing before the change) or
**not run here** (with the reason and the other proof gathered). A subagent's "done" is a claim until the command
is run and read.

### Run log

Each run keeps an append-only log at `<git-dir>/cpipe/run.md` in its worktree: the plan, one row per round,
every workaround, the review findings and their dispositions. It is never committed, and it is how a run resumes.

### Cost

Reading happens once, in planning. While building, only the narrow tests run; the full checks run once per
round. The second review round sees only the changes since the first.

## What it never does

Merge, push, open a pull request, deploy or message anyone without being asked; skip or weaken a test; edit your
own checkout or files outside the plan's scope; put secrets in code, commits or reports; add AI attribution.

## License

MIT
