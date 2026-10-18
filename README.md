# cpipe

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that delivers a coding task to reviewed, verified
code. You hand over the task and wait; cpipe decides open questions itself, plans, writes the tests first, builds,
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
/cpipe <task>
```

`/cpipe:run <task>` does the same. Start the task with `--kanban` to hand the role steps to your profiles as
cards on your Hermes kanban board (see Roles). Say **continue** in the chat to resume a run after a restart.

## Flow

```
set up (worktree + dependencies) → explore → design → decide open questions → plan
   → tests first (RED) → build (GREEN, one verified change at a time) → verify
   → QA on the real surface + independent review, side by side (≤ 2 rounds) → clean up → verify → commit → report
```

A task with several issues becomes one delivery per issue: its own branch, worktree, commit and pull request,
run one after another and reported together. Issues ship together only when one cannot work without the other.

Every step runs on every task. A step that does not apply (design for an internal fix, QA for a docs change)
is skipped with its reason in the run log. The checks and the report are always done by the chat itself.

Your own skills come first: cpipe sets the order of the steps, and at each step the chat loads any of your skills
that covers that work (pull-request content, commit messages, review, UI design, testing) and follows it over
cpipe's defaults. cpipe's guard rails still apply.

Only `cpipe:run` is registered; the steps are files inside it, so they never show up as separate commands.

| File | Step |
|---|---|
| `SKILL.md` | The flow, the rules, the run log, playbooks per task kind |
| `steps/plan.md` | Worktree and dependencies, a flow map of every entrance, exit, consumer and guard, a premortem of what could break, the checks and their baseline, open questions decided as assumptions, plan |
| `steps/design.md` | States, flow, copy, accessibility and edge cases of anything user-facing |
| `steps/build.md` | Tests first (only tests that fail when the code breaks), implement, cleanup pass |
| `steps/verify.md` | Run the checks with honest states, work around checks that will not run, completion gate |
| `steps/qa.md` | Use the change on the real surface, walk the acceptance criteria, try to break it, broader suite |
| `steps/review.md` | Independent review by a subagent that hunts regressions and risks the plan missed, findings sorted into act, consider, noted and dismissed |
| `steps/report.md` | Commit, a draft pull request only when asked (then waits for CI), the report |

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
chat. If a profile fails, the chat retries once, tells you, and does the step itself; a profile that runs past its time
budget is stopped and the chat does the step at once.

With `--kanban`, each handed-over step is a card assigned to its profile, run by the board's dispatcher, so
the work shows on the board. A card that is blocked, times out or is not picked up within 10 minutes counts as a
failed role.

### Playbooks

| Kind | Proof |
|---|---|
| bug | A test that fails before the fix and passes after it |
| feature | New tests red then green, existing checks still green, behavior exercised on the real surface |
| refactor | The same checks green before and after |
| chore | Checks green, change read back |

### It runs to the end

It does not stop: the task is the last message you send. Open questions are decided and listed with their reason
in the report's Decisions table. Things only you may do (credentials, anything destructive or irreversible,
anything leaving your machine you did not ask for) are skipped and named in the report's status line, as is an
open security finding it cannot fix inside the scope. It never asks you to fix or run anything.

### The report

The run reports only once the work is finished: findings fixed, the last fix reviewed, CI waited for when a pull
request was asked for. The report has a status line (`✅ Done`, `✅ Done, test by hand` or `⚠️ Done except …`),
then:

- **Summary:** for a bug, what happened, the cause and the fix; for a feature, what was added and how; for an
  improvement, before, after and why.
- **Decisions:** each choice made, what it was chosen over, and why.
- **Work:** every item from reproduction to CI with ✅, ⚠️, ⏳, ❌ or — and its proof.
- **Manual testing:** only what this machine could not verify, with steps and the expected result.
- **Not in this change** and **Details** (branch, commit, pull request, files, roles, lessons).

### It learns from misses

When review, QA or you catch something the plan should have seen, the run writes a one-line lesson for the
project at `<git-common-dir>/cpipe/lessons.md` (shared by all its worktrees, never committed). Every later run
reads the lessons before planning and checks each one. A message after the report is a follow-up on the same
worktree: it is fixed, verified and reviewed, then reported again.

### Honest states

Every check in the report is one of **pass**, **fail**, **baseline** (already failing before the change) or
**not run here** (with the reason and the other proof gathered). A subagent's "done" is a claim until the command
is run and read.

### Run log

Each run keeps an append-only log at `<git-dir>/cpipe/run.md` in its worktree: the plan, one row per round,
every workaround, the review findings and their dispositions. It is never committed, and it is how a run resumes.
The log also marks the worktree as cpipe's: each new run removes cpipe worktrees whose pull request is merged and
that hold no uncommitted or unpushed work, so finished worktrees do not fill the disk. Other worktrees are never
touched.

### Cost

Reading happens once, in planning. While building, only the narrow tests run; the full checks run once per
round. The second review round sees only the changes since the first.

## What it never does

Merge, push, open a pull request, deploy or message anyone without being asked; skip or weaken a test; edit your
own checkout or files outside the plan's scope; put secrets in code, commits or reports; add AI attribution.

## License

MIT
