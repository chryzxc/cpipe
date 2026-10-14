---
name: build
description: "cpipe: worktree, tests first, implement, clean up."
version: 3.0.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delivery, tdd, implementation, refactor]
    related_skills: [cpipe:run, cpipe:verify]
---

# cpipe: build

Steps 4, 5 and 8 of `cpipe:run`.

## 4. Worktree

1. Create a branch and worktree from the plan's base, beside the repository:
   `git worktree add -b cp/<short-slug> <repo>-worktrees/cp-<short-slug> <base>`. Follow the project's own
   worktree convention instead if its instructions name one. Never build in the user's own checkout.
2. **Dependencies.** A fresh worktree has none installed. Install them inside the worktree the way the project
   does (its lockfile and CI show how): `npm ci`, `pnpm install --frozen-lockfile`, `yarn install --immutable`,
   `uv sync`, `pip install -e .` into the project's virtual environment, and so on. Never install globally, and do
   not change the lockfile unless the task is about dependencies. Copy local env files the project needs
   (`.env`) from the user's checkout only if the checks need them, and never print their contents.
3. Start the run log (see `cpipe:run`).

## 5. Build

If the roles file names a `build` profile, hand this step and the cleanup to it (`roles.md` in `cpipe:run`)
and verify what it reports. Otherwise follow the playbook from the plan. In every playbook:

- **Climb the ladder** before writing code: does it need to exist, does the codebase already have it, does the
  standard library or an installed dependency do it. Then write the minimum that works.
- **One change at a time.** Make a change, run the narrow test, keep it if it moves toward the acceptance
  criteria, otherwise undo it and try a different idea. Never stack two unverified changes.
- **Narrow tests while working**, the plan's full checks at the end of each round (`cpipe:verify`). Do not rerun
  the whole suite after every edit; it costs time and tokens.
- **Side discoveries** that block the acceptance (a broken helper, a wrong fixture): fix them inside the scope and
  note them. Anything outside the scope goes in the report.
- **A round** ends with a row in the run log: what changed, why, the check results.

Commands you cannot run as written (an approval policy blocks inline `-c`/`-e` code, `sh -c` or heredocs): write
the script to a file and run the file, or call the program directly with its arguments. This never ends a step.

## 8. Clean

Once, after the review is clean. Read your whole diff (`git diff <base>...HEAD`) as a stranger would and remove:

- dead code, debug output, commented-out code;
- duplicates of helpers the codebase already has (reuse them);
- abstractions with one caller, configuration for a value that never changes;
- comments that restate the code; keep the ones that explain why.

Then run the checks again. If anything turns red, undo the cleanup change that caused it.
