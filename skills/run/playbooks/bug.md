# Bug playbook

**Proof required:** a test that failed before the fix (RED) and passes after it (GREEN), plus the plan's checks and every must-not-change item still holding.

1. **Reproduce first.** Hit the bug yourself: a test, a command, a small script that calls the code. A bug you
   cannot trigger is a bug you cannot prove you fixed. If it will not reproduce, write down what you tried, make
   the closest failing test you can, and say so in the report.
2. **Find the root cause.** Form two or three hypotheses and eliminate them with evidence (logs, call sites, the
   data). Before editing a function, search every caller: one guard in the shared path beats one per caller.
3. **Write the failing test** that captures the bug, on the surface where it showed up. Run it and see it fail
   for the right reason (an assertion, not an import error). Record the RED output in the run log.
4. **Fix it** with the smallest change the evidence justifies. Every line traces to the root cause; "might help"
   is a second bug.
5. **Run the test again** (GREEN), then the plan's checks via `steps/verify.md`.
6. **Commit order:** the failing test first, then the fix, when the project accepts several commits. Otherwise
   one commit.

If the test cannot run here at all, see `steps/verify.md`: prove the fix another way (a script, the build, a
narrower runner) and record that the test is unverified. Never stop for it.
