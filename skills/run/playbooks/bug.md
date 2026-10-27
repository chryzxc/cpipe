# Bug playbook

**Proof required:** a test that failed before the fix (RED) and passes after it (GREEN), plus the plan's checks and every must-not-change item still holding.

1. **Reproduce first.** Hit the bug yourself: a test, a command, a small script that calls the code. A bug you
   cannot trigger is a bug you cannot prove you fixed. If it will not reproduce, write down what you tried, make
   the closest failing test you can, and say so in the report.
2. **Find the root cause.** Look, do not guess: trace the real values along the flow (a log line, a debugger, a
   small script) for a working case and the failing one; the first place they differ is where the cause is. If
   it worked before, find the commit that broke it with `git bisect run <reproducing test>` between a known good
   commit and the base. Always record where the bug came from: the commit and its pull request that introduced it
   (bisect, or `git blame -w -C` and `git log -S` on the faulty lines, then the commit's pull request), confirmed
   when the test passes at its parent and fails there, or "present since the code was added", or "not
   identified" with what was searched. Never guess from a commit that only touched nearby code.
   Form two or three hypotheses and eliminate them with evidence (logs, call sites, the
   data); ask why until the answer is in the code, not the symptom. Before editing a function, search every
   caller: one guard in the shared path beats one per caller. Search for the same mistake elsewhere (copied
   code, the same pattern) and fix or list each one.
3. **Write the failing test** that captures the bug, on the surface where it showed up. Run it and see it fail
   for the right reason (an assertion, not an import error). Record the RED output.
4. **Fix it** with the smallest change the evidence justifies. Every line traces to the root cause; "might help"
   is a second bug.
5. **Run the test again** (GREEN), then the plan's checks via `steps/verify.md`.
6. **Commit order:** the failing test first, then the fix, when the project accepts several commits. Otherwise
   one commit.

Steps 1 to 3 run in explore, before the plan (`steps/plan.md`, Reproduce), so the plan is built on the proven
cause. The build starts at step 4 from that RED test.

If the test cannot run here at all, see `steps/verify.md`: prove the fix another way (a script, the build, a
narrower runner) and record that the test is unverified. Never stop for it.
