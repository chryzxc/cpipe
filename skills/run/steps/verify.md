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
worker's "it passes" is a claim until you run the command and read the output yourself.

Compare failures with the baseline by name, never by count: save this run's results with
`results <run dir>/results/<check>.head.json` and run `compare <run dir> <check>` (`cpipe:run`, Facts). Every
**new** name is yours to fix, even when the total went down; a check with none is **pass** or **baseline**. Do every
rerun it lists and compare again, until nothing is left to rerun. Find each new failure's cause first (Root cause,
in `steps/build.md`), then up to three fix attempts with different ideas per failure, then change the approach and
loop (`cpipe:run`, Limits). With a build profile, the builder does that in a follow-up: give it each new failure's
name and its failure lines from the log, never the log to read.

Formatters and linters fix only the lines the change touches. A red formatting or lint baseline stays the
baseline: never reformat files or the repository to clear it unless the task asks. A red lint or typecheck
baseline hides nothing: its new errors are found by id like any failure, and each one is yours.

## Fast checks

Tests run for the change, never the whole suite: the full suite takes most of a run's time, and the pull
request's CI runs it. Run each check only where it proves something:

- **Narrow first.** The plan's narrow tests first; the related tests and the build only once they pass. In the
  first verify, start the review then too, before them (`cpipe:run`, Flow): it only reads the code.
- **Related tests, every verify.** Each verify (the first, a fix round, and the last one on the final tree that
  gets committed, `steps/build.md` step 10) runs the narrow tests, the light checks and the tests related to
  every file the change touches since the base, through the runner's related mode (such as
  `vitest related <files>` or `jest --findRelatedTests <files>`); a runner without one runs the test files beside
  the changed files and the ones that import them. The build runs once, in the first verify. With no fix round,
  the first verify's results stand for the last one.
- **Never the full suite.** No verify, QA, review or merge runs the project's whole test suite, and "no
  regressions" does not ask for it: the related tests compared with the base, plus the pull request's CI, are the
  proof. Only a user who asks for the full suite by name gets it. The report lists the full suite as left to CI,
  or as not run when the project has no CI for it.
- **After the baseline.** The baseline (the build and the light checks on the base, `steps/setup.md` step 8) runs
  in its own worktree. The first verify's build starts only once `logs/baseline.done` exists, so two heavy jobs
  never run together; until then run the narrow and related tests and the light checks, then end the turn on the
  baseline's notification.
- **No duplicates.** A subset the related tests already run (a regression script, a focused file) is not run
  beside them.
- **The project's commands only.** Run each check with the project's own command and its reporter flag. No
  wrapper scripts, gates, harnesses, preloads, environment patches, manifests or receipts around it (`cpipe.py`
  only reads the files it writes), and no test runs on other trees
  (another base, both heads, a combined branch) to prove parity: the by-name comparison with the baseline, plus
  the one base check under New failures, is the proof.
- **The runner's own parallelism.** Run the suite the way the project's CI does and let the runner pick its
  workers. Never add serial or single-worker flags (`--runInBand`, a single thread or fork, `--workers=1`) unless
  the project's CI uses them. A parallel option the project already has (such as `pytest -n auto` with
  pytest-xdist installed) is used; one it does not have is never installed.
- **One heavy job at a time.** A large test run and the build each use every core: run them one after the other,
  never together or beside another suite, or both slow down and tests time out. Each takes its turn with `-h`
  (`steps/setup.md`, Time limit), which also holds it while another run's heavy job runs. Lint, typecheck and
  format checks can run alongside.
- **In the background.** The related tests and the build run in the background with their output in a file
  (`logs/verify-<check>.txt`) and a completion notification, under the time limit (`steps/setup.md`, Time
  limit): `perl <run dir>/limit.pl -h <seconds> <command>`, twice the check's time on the base and at least 5
  minutes. A foreground command is cut off after a few minutes and its result is lost; one without a limit can
  hang the run.
- **Same tree, same result.** Write the tree id beside each result. It is the content of the files, so a commit
  does not change it:
  `t=$(mktemp); cp "$(git rev-parse --git-path index)" "$t"; GIT_INDEX_FILE=$t git add -A; GIT_INDEX_FILE=$t git write-tree; rm -f "$t"`.
  A check whose tree id has not changed since it ran is not run again; committing a verified tree keeps its
  results.
- **Names from the runner, not the log.** Every test run also writes its results to a file through the runner's
  machine-readable reporter (`--json --outputFile=<file>` for Jest and Vitest, `--junitxml=<file>` for pytest,
  JUnit XML for the rest), on the base and the delivery alike, and `results` reads that file, never a log that
  can be cut off. Type-check and lint output goes to a file whole (`eslint -f json`, the compiler's plain output)
  and is read the same way, by file, code and message. When `results` says the file is missing or cut off, run
  the check again with the reporter.
- **New failures, once each.** `compare` lists each name that needs a rerun. `rerun-head`: run its file again,
  alone, on the delivery, into `results/<check>.head-alone.json`. `rerun-base`: run only those files on the
  base, with the same command and its time limit, in the base worktree set up added (`now.md`; when it is gone,
  add it again the way `steps/setup.md` step 8 does), into `results/<check>.base-alone.json`. Then compare
  again. **Flaky** (it passed alone) is not a regression, whether or not its cause is known, so it never holds a
  commit, a pull request or a merge; do not build tools to chase its cause. A failure seen while two suites or
  builds ran side by side in one worktree says nothing about the code: the run alone decides. No copies or
  archives of the tree, no further comparisons.
- **The run's own tests are claims.** A test this run wrote can be wrong. When one still fails after a fix
  attempt, or no fix passes it without breaking another test, question the test before the code: check its
  expectation against the base's behavior today (read or run it on the base), the task and the user's words. A
  wrong expectation is fixed in the test, with the reason in the run log and the report; never by weakening a
  check the task asked for.
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
| A tool or profile errors or returns nothing | Read the error, retry once differently, or do the step yourself. |

If nothing works, the check is **not run here**: write the reason and the other proof you gathered, and continue.
The report shows it plainly; that is enough. Never fake a pass, never skip or delete the test to get one.

## Completion gate

Before QA and the review, and before the report. It is searches over the change, not a reading of it, so it
needs no code in the chat. From the worktree root, with the plan's base:

```sh
b=$(git merge-base <base> HEAD)
d() { git diff -U0 "$b"; git ls-files -z --others --exclude-standard | xargs -0 -I{} git diff -U0 --no-index /dev/null {}; }
d | grep -E '^\+\+\+ |^\+.*(TODO|FIXME|XXX|NotImplemented|\.only\(|[.@]skip\b|xfail|XCTSkip|@Disabled|t\.Skip\(|debugger)'
a() { d | grep -v '^+++ '; }
a | grep -iE '^\+.*(BEGIN [A-Z ]*PRIVATE KEY|(api[_-]?key|secret|token|passw(or)?d)[^=:]{0,20}[:=])'
a | grep -E '^\+.*(@ts-(ignore|expect-error|nocheck)|eslint-disable|type: *ignore|noqa|nolint|NOLINT|#\[allow\(|'\
'@SuppressWarnings|swiftlint:disable|\bas any\b|: any\b|catch *(\([^)]*\))? *\{ *\}|except[^:]*: *pass)'
```

The first search lists every changed or new file (`+++ b/<path>`) with its hits beneath it; the second lists
added lines that look like a credential; the third, added escape hatches: a silenced type or lint error, an
`any`, an empty catch.

- **Scope:** every listed file is in the plan's Scope, or the builder's reply or the run log gives the reason.
- **Markers:** each hit is judged from its line alone: a skipped, focused or expected-to-fail test, a `TODO` or
  placeholder, or a literal credential fails the gate; a variable named `token` does not.
- **Escape hatches:** each hit of the third search fails unless the type or rule cannot be met another way and
  the line or the one above says why. An empty catch fails unless the error is expected and the comment says so.
- **Plan done:** every plan step is done, or its correction is in the reply or the run log.
- **Proof:** each acceptance criterion has a test, a check or a recorded manual verification in the reply or the
  run log. Whether the tests assert something real is the review's to judge (`steps/review.md`, Proof).
- **File size:** every file the change adds or grows is within the budget (below).
- **Proof by undo:** every new test of an acceptance rule fails with the fix taken out (below).
- **Port:** every row of the inventory is ported or has its reason (`playbooks/port.md`).

Fix what fails the gate (with a build profile, as one follow-up), then verify again.

### Proof by undo

A test that passes with or without the fix proves nothing, however it was written. Once the narrow tests pass,
while nobody reads or edits the worktree (before the review starts), take the fix out of the source files,
never the tests, run the new tests, and put it back. From the worktree root, with the plan's base, the changed
source files that are not tests, and the narrow command for the new tests:

```sh
b=$(git merge-base <base> HEAD)
git diff "$b" -- <source files> > <run dir>/logs/fix.patch && git apply -R <run dir>/logs/fix.patch
<new tests> > <run dir>/logs/undo.txt 2>&1; echo "exit $?"; git apply <run dir>/logs/fix.patch; git diff --stat "$b"
```

Each new test of an `A` rule must fail in `undo.txt`; tests of must-not-change items pass either way. One that
passes there catches nothing: it goes to the builder with the gate's other items, to make it fail without the
fix. The tree id after must be the one before. A fix that lives only in new files the tests import directly
cannot be taken out this way: record that, and the review judges those tests (`steps/review.md`, Proof). Record
in the run log `undo: <n> of <m> new tests RED`.

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
check from the plan, with the build from the first verify. `compare` lists no new name and nothing to rerun for
any check (an approach that fails three times is replaced, `cpipe:run`, Limits), the completion gate passes, and
this step's items in `track.json` are closed with their evidence.
