# cpipe: the qa step

Step 8 of `cpipe:run`, after the checks pass, at the same time as the review (`steps/review.md`). Checks prove the code; QA proves the behavior. If the roles file
names a `qa` profile, hand this step to it (`roles.md` in `cpipe:run`). Otherwise do it yourself.

## When it applies

Every change with behavior a user or another system can reach. A change with none (docs, comments, a rename the
checks cover) gets `QA: not needed (<reason>)` in the run log.

## Steps

Use the project's own dev server, test runner and tools. Write your own harness only for a behavior they cannot
reach, and keep it small. Evidence is the command or action and what you saw; no hash manifests or copies of the
tree.

1. **Use it on the real surface.** Start the app, CLI or service from the worktree the way the project does (its
   scripts, its dev-server setup), and start it again if it stops, and use the change as a user would: the page in a browser if you have one, the
   endpoint with a real request, the command with real arguments.
2. **Walk the acceptance criteria** and the design note's states one by one. Record what you did and what you
   saw for each.
3. **Try to break it.** Bad, empty and long input, double submit, refresh mid-flow, a slow or failed dependency,
   a user without permission.
4. **Walk the flow map.** Use each entrance and consumer in the plan's flow map, not only the one the task
   named, and check each must-not-change item and each risk whose check is a QA step. Where behavior differs by role,
   tenant, locale or state, repeat it for each (a seeded or test user per role). Then the broader test suite:
   if verify already ran it on this same tree, use that result; otherwise run it once. Compare its failures with
   the baseline: only new failures count.
5. **Clean up** what you started: remove temporary data, and stop the servers unless the report will hand the
   running app to the user (`steps/report.md`, Running app).

## Results

Record each item in the run log as **pass**, **fail** or **not run here**, with the evidence: the command and its
output, the request and response, a screenshot path.

- **fail:** reproduce it, then fix it in step 6 together with the review's **act** findings, in one round. If the
  plan did not foresee it, write a lesson (`cpipe:run`, Lessons). Verify, then QA the failed items again
  alongside the delta review.
- **not run here:** the surface needs something this machine does not have (a login, a device, a live
  service). Use the closest stand-in (a test double, a seeded user, a direct call), then list what is left in
  the report's Test it table, with exact steps and the expected result. Never stop for it.

## Rules

- QA does not edit the repository (code, tests or config). A failure goes back to the build step.
- Local only: never use production systems, real customer data or real accounts, and never send real emails,
  messages or payments. Use test data and the project's local or test setup.
- Every result has evidence; "looks fine" is not a result.
- A QA result from a profile or subagent is a claim: reproduce a failure yourself before acting on it.
- Stop every server and process you started, apart from the one app the report hands over.

## Done when

Every acceptance criterion, design state, must-not-change item and QA risk has a QA result with evidence, the broader suite is compared with the
baseline, and nothing started for QA is still running apart from that app.
