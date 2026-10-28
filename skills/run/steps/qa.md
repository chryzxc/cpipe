# cpipe: the qa step

Step 8 of `cpipe:run`, once the related tests and the build finish, while the review (`steps/review.md`) runs.
Checks prove the code; QA proves the behavior. Hand this step to the `qa` profile, else to this chat's own profile
and model in a session of its own (`roles.md` in `cpipe:run`).

## When it applies

A change with behavior a user or another system can reach, on a surface QA can drive on this machine: a web page
with a browser tool, an HTTP endpoint, a CLI, a library call by script. It is skipped, with
`QA: not needed (<reason>)` in the run log, when:

- the change has no such behavior (docs, comments, a rename the checks cover);
- the checks already exercise it end to end, the way a user would reach it;
- the surface cannot be driven here: a desktop or mobile app, a device, a signed-in external service. The tests
  are the proof, and the report's Test it table lists the manual checks with exact steps and expected results.

Signing in to the project's own app on this machine is not an external service: create a local test user (the
project's seed, fixtures or sign-up page), sign in and run QA behind the login.

## Steps

Use the project's own dev server, test runner and tools. Write your own harness only for a behavior they cannot
reach, and keep it small. Evidence is the command or action and what you saw; no hash manifests or copies of the
tree.

1. **Use it on the real surface.** Start the app, CLI or service from the worktree the way the project does (its
   scripts, its dev-server setup), and start it again if it stops, and use the change as a user would: the page in
   a browser, the endpoint with a real request, the command with real arguments. First confirm it runs this tree's
   code: started from this worktree after the last change, with no stale build, bundle or cache in the way.
2. **Walk the acceptance criteria** and the design note's states one by one. Record what you did and what you
   saw for each.
3. **Look at the UI.** When the change touches what a user sees, judge it by eye, not only by its tests. Use the
   project's own browser test setup when it has one, so the shots repeat; otherwise the browser and image tools
   you have. Screenshot each changed page and state on the base and on this tree, with the same data, at a
   desktop and a phone width, and in dark mode when the app has one; also each page the flow map says must not
   change. Look at each pair against the acceptance criteria and the design note: the intended change is there,
   and nothing else broke (overlap, cut-off text, spacing, alignment, contrast, an empty, loading or error state
   that looks wrong). A visible difference on a page that must not change is a fail. New console errors or failed
   requests on these pages are fails too. A pair you cannot judge with confidence is **not run here**, never a
   guessed pass. Save the shots in `<run dir>/qa/` as `<n>-<page>-before.png` and `<n>-<page>-after.png`. For a
   change users see as text (an endpoint's response, a command's output, an email or message), save the before
   and after the same way as `.txt`, trimmed to what changed. The report shows these pairs (Before and after).
4. **Try to break it.** Bad, empty and long input, double submit, refresh mid-flow, a slow or failed dependency,
   a user without permission.
5. **Walk the flow map.** Use each entrance and consumer in the plan's flow map, not only the one the task named,
   and each row of the behavior table you can reach, as that actor and with that data: a changed row gives its
   After result, every other row today's result. Check each must-not-change item and each risk whose check is a QA
   step. Where behavior differs by role, tenant, locale or state, repeat it for each (a seeded or test user per
   role). Then the related tests: use verify's result for this tree (`steps/verify.md`, Fast checks); run them
   yourself only when verify has none, and never the full suite. Compare
   its failures with the baseline: only new failures count.
6. **Clean up** what you started: remove temporary data, and stop the servers unless the report will hand the
   running app to the user (`steps/report.md`, Running app).

## Results

Record each item in the run log as **pass**, **fail** or **not run here**, with the evidence: the command and its
output, the request and response, a screenshot path.

- **fail:** it goes to the fix round with the review's **act** findings (`steps/review.md`, section 3): the
  builder writes its Cause line, reproduces it with a test that fails for that cause, then fixes it at the shared
  point (`steps/build.md`, Root cause). If the plan did not foresee it, write a learning (`learning.md` beside
  `cpipe:run`). Verify, then QA the failed items again alongside the delta review; that re-run is the last QA round,
  and a failure in it is fixed and proven by its test the same way, without another re-run.
- **not run here:** the surface needs something this machine does not have (a login, a device, a live
  service). Use the closest stand-in (a test double, a seeded user, a direct call), then list what is left in
  the report's Test it table, with exact steps and the expected result. Never stop for it. When the reason is
  a missing sign-in or credential (no test account, an expired session, a key not set), record which service and
  what is missing, so the report names it every time.

## Rules

- QA does not edit the repository (code, tests or config). A failure goes back to the builder.
- Local only: never use production systems, real customer data or real accounts, and never send real emails,
  messages or payments. Use test data and the project's local or test setup.
- Every result has evidence; "looks fine" is not a result.
- A QA result from a profile is a claim: a failure is reproduced by a failing test before it is
  fixed (by the builder, or by you without a build profile).
- Stop every server and process you started, apart from the one app the report hands over.

## Done when

Every acceptance criterion, design state, must-not-change item and QA risk has a QA result with evidence, the
broader suite is compared with the baseline, and nothing started for QA is still running apart from that app.
