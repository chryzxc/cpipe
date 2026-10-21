# cpipe: the build step

Steps 6 and 10 of `cpipe:run`. With a build profile, the builder does step 6 in its first brief, from its own plan
or a plan profile's `plan.md`, and every fix after it as a follow-up of the same session (`roles.md` in
`cpipe:run`); you verify what it reports with `steps/verify.md`. Otherwise do them yourself.

## 6. Build

Follow the plan's steps in order (`steps/plan.md`, Steps); the playbook says what proof the kind needs. A step
that turns out wrong (the code is not where it says, its test cannot fail as written, a helper to reuse turns up)
is never improvised silently: record the correction (what was wrong, what instead), then follow it. In
every playbook:

1. **Tests first.** Write only the tests the plan lists, and see each fail for the right reason (an assertion,
   not an import error) before writing the change. The playbook says what proof the kind needs. A test written
   in explore (`steps/plan.md`, Reproduce) is already one of them: run it once to see it is still RED.
   A test of behavior that already exists (pinning a must-not-change item) passes from the start, so prove it
   instead: break the line it guards for a moment, see the test fail, and restore it. A test that still passes
   with its code broken catches nothing: fix it or delete it. A table-driven test of the behavior table takes two
   moves: today's results first (it passes; prove it as above), then only the changed rows set to their After
   results, which fail until the change is made.
2. **Climb the ladder** before writing code: does it need to exist, does the codebase already have it, does the
   standard library or an installed dependency do it. Then write the minimum that works. Keep every file you
   add or grow within the file size budget (`steps/verify.md`, File size): new logic for a large file goes in a
   new file beside it.
3. **One change at a time.** Make a change, run the narrow test, keep it if it moves toward the acceptance
   criteria, otherwise undo it and try a different idea. Never stack two unverified changes.
4. **Narrow tests only.** The full suite and a slow production bundle run in `steps/verify.md` (Fast checks),
   and only those results count; do not run them here as well.
5. **It builds before it leaves.** Before the round ends, run the fastest command that compiles the change: the
   project's typecheck, or for a compiled language its compiler's build (such as `swift build`, `cargo check`,
   `go build ./...` or `tsc --noEmit`), and the plan's narrow tests, and fix what they report. Code that does not
   build never leaves a round: a compile error found in verify costs a whole extra round.
6. **Clean before it leaves.** Read your own diff (`git diff` plus new files) once as a stranger would and
   remove:
   - dead code, debug output, commented-out code;
   - duplicates of helpers the codebase already has (reuse them);
   - abstractions with one caller, configuration for a value that never changes;
   - comments that restate the code; keep the ones that explain why;
   - tests that catch no break: duplicates of an existing test, tests of constants, getters, the framework or
     their own mocks, assertions so loose any output passes. Merge near-duplicates into one table-driven test.
   Then the build and the narrow tests once more if anything changed.
7. **A round** ends with a row in the run log: what changed, why, the check results (a build profile puts it in
   its reply instead).
8. **Read little.** The plan names the files and lines: read those ranges, search before opening a whole file,
   and never read again a file whose text you already have. Test output goes to a file beside
   the run log (`logs/build-<test>.txt`, never in the worktree); read its summary and the failures.

**A fix round** (a failing check, a review finding, a QA failure, a conflict) gets the same rules. Check each
item against the code first. A real one is fixed, with a failing test first when it is about behavior: a QA
failure is reproduced by that test before the fix. One the code shows is not real is left alone and answered
with the evidence (file and line, a test or a command's output). Reply per item: fixed (how, and the test) or not
real (the evidence). A conflict from an update onto a moved base is resolved keeping what both sides meant.

Side discoveries that block the acceptance (a broken helper, a wrong fixture): fix them inside the scope and note
them. Anything outside the scope goes in the report.

Commands you cannot run as written (an approval policy blocks inline `-c`/`-e` code, `sh -c` or heredocs): write
the script to a file and run the file, or call the program directly with its arguments. This never ends a step.

## 10. Clean

The cleanup is part of every round (step 6, Clean before it leaves), done while the code is still in context,
so it costs no extra round. Step 10 is the last verify (`steps/verify.md`) on the final tree, with the full suite
and the build; when the tree id is the one the first verify passed on, its results stand and nothing runs again.
If it turns red, the builder fixes or undoes the cause (a follow-up), and the last verify runs again. That verify
is the proof: no review or QA round follows it.

## Rules

- Only the plan's scope. Never reformat, rename or "improve" code the task does not need.
- Follow the project's style, patterns and instructions over your own preferences.
- Never skip, weaken or delete a test, and never add `.only`, `skip` or expected-failure markers.
- Never hard-code secrets, test credentials or environment-specific paths.
- No new dependency unless the plan names it.
- Do not commit in this step; the report step commits (or the early draft, `steps/report.md`).

## Done when

Every acceptance criterion has a test or a recorded check, the build or typecheck and the narrow tests pass,
the diff is clean, and the round is in the run log. For step 10: the last verify passed on the final tree.
