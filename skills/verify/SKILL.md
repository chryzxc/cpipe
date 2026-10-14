---
name: verify
description: "cpipe: run checks honestly and work around blockers."
version: 3.0.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delivery, verification, testing, evidence]
    related_skills: [cpipe:run, cpipe:build]
---

# cpipe: verify

Step 6 of `cpipe:run`, and whenever a playbook says to verify.

## Run the checks

Run every check from the plan on the current tree, in the worktree. Read the output yourself. Record each check
in the run log with one of four honest states:

| State | Means |
|---|---|
| **pass** | You ran it on this tree and read a pass. |
| **fail** | You ran it and it failed. Name the failing tests or errors. |
| **baseline** | It fails the same way on the base, before your change. Not caused by you. |
| **not run here** | It cannot run on this machine. Say why and how you verified instead. |

A result counts only for the tree it ran on. After any change, earlier results are stale: run them again. A
worker's or subagent's "it passes" is a claim until you run the command and read the output yourself.

Compare failures with the baseline: only new failures are yours to fix. Three fix attempts with different ideas
per failure, then record it as open and move on (`cpipe:run`, limits).

## When a check will not run

Never stop the run for this. Work down the list; the first thing that works wins. Record what you did.

| Obstacle | Do |
|---|---|
| The approval policy blocks the command (`sh -c`, inline `-c`/`-e`, heredoc, pipes) | Run the program directly with its arguments, or write a script file and run it. |
| Command or module not found | Install the project's dependencies in the worktree (`cpipe:build`), use the project's own binary (`node_modules/.bin/…`, `.venv/bin/…`, `npx`, `uv run`), or the runner the CI workflow uses. |
| Wrong runtime version | Use the version the project pins (`.nvmrc`, `.tool-versions`, `engines`, `requires-python`) through the version manager that is installed. |
| The test framework itself crashes on the base too (a tooling mismatch, an old transpiler) | Baseline problem. Run the test another way: a single file, the project's other runner, or a small script that imports and calls the code. Typecheck and build count as partial proof. Record the original check as not run here. |
| Needs a service (database, browser, device, network API) that is not available | Use the project's test doubles or in-memory setup if it has one. Otherwise verify the logic with unit tests and record the end-to-end part as not run here. |
| Times out | Run a narrower subset (the changed files' tests), raise the timeout once, look for a hang you caused. |
| Flaky | Run it again once. A different result twice means flaky: record both runs. |
| Expected report or output file is missing | Judge by the exit code and the printed summary. |
| A tool or subagent errors or returns nothing | Read the error, retry once differently, or do the step yourself. |

If nothing works, the check is **not run here**: write the reason and the other proof you gathered, and continue.
The report shows it plainly; that is enough. Never fake a pass, never skip or delete the test to get one.

## Completion gate

Before the review and before the report, check the diff (`git diff <base>...HEAD`):

- Tests you added actually assert something, and none are skipped, focused (`.only`) or marked to fail.
- No `TODO`, `FIXME`, `NotImplemented`, placeholder values or debug output left in changed lines.
- Every changed file is inside the plan's scope, or the reason is in the run log.
- No credentials, tokens, keys or private URLs in the diff.
- The acceptance criteria each have proof: a test, a check, or a recorded manual verification.

Fix what fails the gate, then verify again.
