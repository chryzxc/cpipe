---
name: build
description: "cpipe: tests first, implement, clean up."
version: 3.5.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delivery, tdd, implementation, refactor]
    related_skills: [cpipe:run, cpipe:verify]
---

# cpipe: build

Steps 6 and 10 of `cpipe:run`. If the roles file names a `build` profile, hand both steps to it (`roles.md` in
`cpipe:run`), then verify what it reports with `cpipe:verify`. Otherwise do them yourself.

## 6. Build

Follow the playbook from the plan. In every playbook:

1. **Tests first.** Write the tests for the acceptance criteria and see them fail for the right reason (an
   assertion, not an import error) before writing the change. The playbook says what proof the kind needs.
2. **Climb the ladder** before writing code: does it need to exist, does the codebase already have it, does the
   standard library or an installed dependency do it. Then write the minimum that works.
3. **One change at a time.** Make a change, run the narrow test, keep it if it moves toward the acceptance
   criteria, otherwise undo it and try a different idea. Never stack two unverified changes.
4. **Narrow tests while working**, the plan's full checks at the end of each round (`cpipe:verify`).
5. **A round** ends with a row in the run log: what changed, why, the check results.

Side discoveries that block the acceptance (a broken helper, a wrong fixture): fix them inside the scope and note
them. Anything outside the scope goes in the report.

Commands you cannot run as written (an approval policy blocks inline `-c`/`-e` code, `sh -c` or heredocs): write
the script to a file and run the file, or call the program directly with its arguments. This never ends a step.

## 10. Clean

Once, after the review. Read the whole diff (`git diff <base>...HEAD` plus uncommitted changes) as a stranger
would and remove:

- dead code, debug output, commented-out code;
- duplicates of helpers the codebase already has (reuse them);
- abstractions with one caller, configuration for a value that never changes;
- comments that restate the code; keep the ones that explain why.

Then run the checks again. If anything turns red, undo the cleanup change that caused it.

## Rules

- Only the plan's scope. Never reformat, rename or "improve" code the task does not need.
- Follow the project's style, patterns and instructions over your own preferences.
- Never skip, weaken or delete a test, and never add `.only`, `skip` or expected-failure markers.
- Never hard-code secrets, test credentials or environment-specific paths.
- No new dependency unless the plan names it.
- Do not commit in this step; the report step commits.

## Done when

Every acceptance criterion has a test or a recorded check, the narrow tests pass, and the round is in the run
log. For step 10: the cleanup pass is done and the checks are back to their state before it.
