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
`baseline-<check>.txt` (`steps/setup.md`, step 8) and run `comm -13 baseline-<check>.txt now-<check>.txt`.
Every id it prints is new and yours to fix, even when the total went down; no output means the check is
**baseline**. Find each new failure's cause first, the way `playbooks/bug.md` step 2 does, then up to three fix
attempts with different ideas per failure, then record it as open and move on
(`cpipe:run`, limits). With a build profile, the builder does that in a follow-up: give it each new failure's
name and its failure lines from the log, never the log to read.

Formatters and linters fix only the lines the change touches. A red formatting or lint baseline stays the
baseline: never reformat files or the repository to clear it unless the task asks.

## Fast checks

The full suite and the production build take most of a run's time. Run each only where it proves something:

- **Narrow first.** The plan's narrow tests first; the full suite and the build only once they pass. In the first
  verify, start the review then too, before them (`cpipe:run`, Flow): it only reads the code.
- **When.** The full suite and the build run in the first verify and in the last one, on the final tree that
  gets committed (`steps/build.md`, step 10); with no fix round between them, the first verify's results stand
  for the last one (Same tree, same result). A verify between them (a fix round) runs the narrow tests plus the
  tests related to the files changed since the last full run, through the runner's related or changed mode (such
  as `vitest related <files>` or `jest --findRelatedTests <files>`); a runner without one runs the test files
  beside the changed files.
- **After the baseline.** The baseline runs in its own worktree (`steps/setup.md`, step 8). The first verify's
  full suite and build start only once `logs/baseline.done` exists, so two heavy jobs never run together; until
  then run the narrow tests and the light checks, then end the turn on the baseline's notification. A full suite
  the baseline did not finish (`exit 124`, or its log stops before the runner's summary) hangs or outruns its
  limit there and would here too: no verify runs it whole. Each one runs the narrow tests and the tests related
  to every file the change touches (as between full runs, below), and the report lists the full suite as not
  finished on the base.
- **No duplicates.** A subset the full suite already runs (a regression script, a focused file) is not run beside
  it.
- **The project's commands only.** Run each check with the project's own command. No wrapper scripts, gates,
  harnesses, preloads, environment patches, manifests or receipts around it, and no full suites on other trees
  (another base, both heads, a combined branch) to prove parity: the by-name comparison with the baseline, plus
  the one base check under New failures, is the proof.
- **The runner's own parallelism.** Run the suite the way the project's CI does and let the runner pick its
  workers. Never add serial or single-worker flags (`--runInBand`, a single thread or fork, `--workers=1`) unless
  the project's CI uses them. A parallel option the project already has (such as `pytest -n auto` with
  pytest-xdist installed) is used; one it does not have is never installed.
- **One heavy job at a time.** The full suite and the build each use every core: run them one after the other,
  never together or beside another suite, or both slow down and tests time out. Each takes its turn with `-h`
  (`steps/setup.md`, Time limit), which also holds it while another run's heavy job runs. Lint, typecheck and
  format checks can run alongside.
- **In the background.** The full suite and the build run in the background with their output in a file
  (`logs/verify-<check>.txt`) and a completion notification, under the time limit (`steps/setup.md`, Time
  limit): `perl <run dir>/limit.pl -h <seconds> <command>`, twice the check's time on the base and at least 5
  minutes. A foreground command is cut off after a few minutes and its result is lost; one without a limit can
  hang the run.
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
  file out); then check it on the base once: run only those files, with the same command and its time limit, in
  the base worktree set up added (`now.md`). When it is gone, add it again the way `steps/setup.md` step 8
  does. Failing there too is **baseline**; passing there is yours to fix. One round, then decide: no copies or
  archives of the tree, no further comparisons.
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
| Times out | Run the failing files alone (Fast checks, New failures), raise the timeout once, look for a hang you caused. A suite that also hit its limit on the base is not finished there (Fast checks, After the baseline). |
| Flaky | Run it again once, alone. A different result twice means flaky: record both runs. |
| Expected report or output file is missing | Judge by the exit code and the printed summary. |
| A tool or subagent errors or returns nothing | Read the error, retry once differently, or do the step yourself. |

If nothing works, the check is **not run here**: write the reason and the other proof you gathered, and continue.
The report shows it plainly; that is enough. Never fake a pass, never skip or delete the test to get one.

## Completion gate

Before QA and the review, and before the report. It is searches over the change, not a reading of it, so it
needs no code in the chat. From the worktree root, with the plan's base:

```sh
b=$(git merge-base <base> HEAD)
d() { git diff -U0 "$b"; git ls-files -z --others --exclude-standard | xargs -0 -I{} git diff -U0 --no-index /dev/null {}; }
d | grep -E '^\+\+\+ |^\+.*(TODO|FIXME|XXX|NotImplemented|\.only\(|[.@]skip\b|xfail|XCTSkip|@Disabled|t\.Skip\(|debugger)'
d | grep -iE '^\+[^+].*(BEGIN [A-Z ]*PRIVATE KEY|(api[_-]?key|secret|token|passw(or)?d)[^=:]{0,20}[:=])'
```

The first search lists every changed or new file (`+++ b/<path>`) with its hits beneath it; the second lists
added lines that look like a credential.

- **Scope:** every listed file is in the plan's Scope, or the builder's reply or the run log gives the reason.
- **Markers:** each hit is judged from its line alone: a skipped, focused or expected-to-fail test, a `TODO` or
  placeholder, or a literal credential fails the gate; a variable named `token` does not.
- **Plan done:** every plan step is done, or its correction is in the reply or the run log.
- **Proof:** each acceptance criterion has a test, a check or a recorded manual verification in the reply or the
  run log. Whether the tests assert something real is the review's to judge (`steps/review.md`, Proof).
- **File size:** every file the change adds or grows is within the budget (below).
- **Port:** every row of the inventory is ported or has its reason (`playbooks/port.md`).

Fix what fails the gate (with a build profile, as one follow-up), then verify again.

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
