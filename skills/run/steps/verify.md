# cpipe: the verify step

Step 7 of `cpipe:run`, and whenever a playbook or another step says to verify. Always yours: never handed to a
profile, because its states are what the report trusts.

## Run the checks

Run the plan's checks on the current tree, in the worktree, as Fast checks below says. Read the output yourself. Record each check
in the run log with one of four honest states:

| State | Means |
|---|---|
| **pass** | You ran it on this tree and read a pass. |
| **fail** | You ran it and it failed. Name the failing tests or errors. |
| **baseline** | It fails the same way on the base, before your change. Not caused by you. |
| **not run here** | It cannot run on this machine. Say why and how you verified instead. |

A result counts only for the tree it ran on. After any change, earlier results are stale: run them again. A
worker's or subagent's "it passes" is a claim until you run the command and read the output yourself.

Compare failures with the baseline by name, never by count: save this run's failing ids the same way as
`baseline-<check>.txt` (`steps/plan.md`, Baseline) and run `comm -13 baseline-<check>.txt now-<check>.txt`.
Every id it prints is new and yours to fix, even when the total went down; no output means the check is
**baseline**. Three fix attempts with different ideas per failure, then record it as open and move on
(`cpipe:run`, limits).

Formatters and linters fix only the lines the change touches. A red formatting or lint baseline stays the
baseline: never reformat files or the repository to clear it unless the task asks.

## Fast checks

The full suite and the production build take most of a run's time. Run each only where it proves something:

- **Narrow first.** The plan's narrow tests first; the full suite and the build only once they pass.
- **When.** The full suite and the build run in the first verify and in the last one, after the cleanup
  (`steps/build.md`, step 10), on the tree that gets committed. A verify between them (a fix round) runs the
  narrow tests plus the tests related to the files changed since the last full run, through the runner's related
  or changed mode (such as `vitest related <files>` or `jest --findRelatedTests <files>`); a runner without one
  runs the test files beside the changed files.
- **No duplicates.** A subset the full suite already runs (a regression script, a focused file) is not run beside
  it.
- **The runner's own parallelism.** Run the suite the way the project's CI does and let the runner pick its
  workers. Never add serial or single-worker flags (`--runInBand`, a single thread or fork, `--workers=1`) unless
  the project's CI uses them. A parallel option the project already has (such as `pytest -n auto` with
  pytest-xdist installed) is used; one it does not have is never installed.
- **One heavy job at a time.** The full suite and the build each use every core: run them one after the other,
  never together or beside another suite, or both slow down and tests time out. Lint, typecheck and format checks
  can run alongside.
- **In the background.** The full suite and the build run in the background with their output in a file
  (`logs/verify-<check>.txt`) and a completion notification. A foreground command is cut off after a few minutes
  and its result is lost.
- **Same tree, same result.** Write the tree id beside each result. It is the content of the files, so a commit
  does not change it:
  `t=$(mktemp); cp "$(git rev-parse --git-path index)" "$t"; GIT_INDEX_FILE=$t git add -A; GIT_INDEX_FILE=$t git write-tree; rm -f "$t"`.
  A check whose tree id has not changed since it ran is not run again; committing a verified tree keeps its
  results.
- **New failures, once each.** A failure that is not in the baseline: run its file again, alone, once. A test
  that passes alone is flaky or load-sensitive: record both runs; it is not a regression, whether or not its cause
is known, so it never holds a commit, a pull request or a merge. Do not build tools to chase its cause. A failure
seen while two suites or builds ran side by side in one worktree says nothing about the code: the run alone
decides. One that also fails
  alone is yours to fix, unless the baseline has no result for it (it did not run, did not finish, or left that
  file out); then check it on the base once: add a temporary worktree of the plan's base commit
  (`git worktree add --detach "$(mktemp -d)/base" <base commit>`),
  link the delivery worktree's installed dependencies into it (or install them, when the change touched the
  dependency files), run only those files there with the same command, then
  `git worktree remove --force <path>`. Failing there too is **baseline**; passing there is yours to fix. One
  round, then decide: no copies or archives of the tree, no further comparisons.
- **Stop what is done.** A check this run started that hung or was replaced is stopped by its process id; none is
  left running when the step ends.

## When a check will not run

Never stop the run for this. Work down the list; the first thing that works wins. Record what you did.

| Obstacle | Do |
|---|---|
| The approval policy blocks the command (`sh -c`, inline `-c`/`-e`, heredoc, pipes) | Run the program directly with its arguments, or write a script file and run it. |
| Command or module not found | Install the project's dependencies in the worktree (`steps/build.md`), use the project's own binary (`node_modules/.bin/…`, `.venv/bin/…`, `npx`, `uv run`), or the runner the CI workflow uses. |
| Wrong runtime version | Use the version the project pins (`.nvmrc`, `.tool-versions`, `engines`, `requires-python`) through the version manager that is installed. |
| The test framework itself crashes on the base too (a tooling mismatch, an old transpiler) | Baseline problem. Run the test another way: a single file, the project's other runner, or a small script that imports and calls the code. Typecheck and build count as partial proof. Record the original check as not run here. |
| The project's own app, server or local service is not running, or stopped mid-run | Start it again the way the project does (its dev script, its local setup), wait until it answers, and continue. Only a service this machine cannot run is not run here. |
| Needs a service (database, browser, device, network API) that is not available | Use the project's test doubles or in-memory setup if it has one. Otherwise verify the logic with unit tests and record the end-to-end part as not run here. |
| Times out | Run the failing files alone (Fast checks, New failures), raise the timeout once, look for a hang you caused. |
| Flaky | Run it again once, alone. A different result twice means flaky: record both runs. |
| Expected report or output file is missing | Judge by the exit code and the printed summary. |
| A tool or subagent errors or returns nothing | Read the error, retry once differently, or do the step yourself. |

If nothing works, the check is **not run here**: write the reason and the other proof you gathered, and continue.
The report shows it plainly; that is enough. Never fake a pass, never skip or delete the test to get one.

## Completion gate

Before QA and the review, and before the report, check the diff (`git diff <base>...HEAD`):

- Tests you added actually assert something, and none are skipped, focused (`.only`) or marked to fail.
- No `TODO`, `FIXME`, `NotImplemented`, placeholder values or debug output left in changed lines.
- Every changed file is inside the plan's scope, or the reason is in the run log. No file changed only by
  formatting.
- Every plan step is done, or its correction is in the run log.
- No credentials, tokens, keys or private URLs in the diff.
- The acceptance criteria each have proof: a test, a check, or a recorded manual verification.
- Every file the change adds or grows is within the file size budget (below).
- For a port, every row of the inventory is ported or has its reason (`playbooks/port.md`).

Fix what fails the gate, then verify again.

### File size

Large files cost every later reader: they are read in pieces and edited blind. The budget is 5,000 tokens per
file (tokens = bytes / 4, about 500 lines), for source and test code. A file fails when it is new and over the
budget, when the change takes it over, or when it was already over and grew by more than 100 tokens. If the
project has its own size rule (a lint `max-lines` setting, its instructions), use that instead. From the worktree
root, with the plan's base:

```sh
b=$(git merge-base <base> HEAD)
{ git diff --name-only --diff-filter=AM "$b"; git ls-files --others --exclude-standard; } | sort -u |
while IFS= read -r f; do
  [ -f "$f" ] || continue
  new=$(( $(wc -c < "$f") / 4 )); old=$(( $(git cat-file -s "$b:$f" 2>/dev/null || echo 0) / 4 ))
  [ "$new" -gt 5000 ] && { [ "$old" -le 5000 ] || [ $((new - old)) -gt 100 ]; } && echo "$f: $old -> $new tokens"
done
```

No output passes. Skip listed files that are not written by hand: lockfiles, generated code (a "generated"
header, `linguist-generated` in `.gitattributes`, codegen output), vendored or minified files, snapshots,
fixtures and test data, migrations, and data files. Split every other listed file:

- **By responsibility.** Move one cohesive piece (a class, a group of related functions, a sub-component) to a
  new file beside it, named for what it does; never `part2` or `utils2`.
- **Imports keep working.** The original file re-exports what moved, or every importer is updated in this diff.
- **Tests move with their code.** Behavior does not change; run the narrow tests and the checks again.
- **A file that was already over the budget:** do not split its old code (that is outside the scope). Move your
  new code into a new file and call it from the old one, so the old file grows by the import and the call only.

## Rules

- A state is only what you saw on this tree. No result is carried over from before a change; a result on the
  same tree id is reused (Fast checks).
- Never fake a pass, never skip or delete a test, never edit a check's command or config to make it pass.
- Never run checks against production systems or real customer data.
- Workarounds are recorded, every one, with what they prove and what they do not.

## Done when

Every check this verify runs (Fast checks) has a state on the current tree; in the last verify, that is every
check from the plan. Every new failure is fixed or recorded as open after three attempts, and the completion gate
passes.
