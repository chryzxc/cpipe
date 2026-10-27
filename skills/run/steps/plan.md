# cpipe: the plan step

Steps 2, 4 and 5 of `cpipe:run` (set up is `steps/setup.md`, design `steps/design.md`). Spend the reading here,
once, so the build does not repeat it. A plan profile does these steps in its brief and the builder builds from
its plan; with a build profile and no plan profile, the builder does them in its first brief and builds right
after (`roles.md` in `cpipe:run`). What they say to record in the run log, the profile writes in `plan.md`.

## 2. Explore

A plan profile does this step, or without one the builder. With an `explore` profile and neither, have it map
the code (`roles.md` in `cpipe:run`) and read the lines around each place it cites to confirm. Otherwise do it
yourself:

1. **Instructions.** Read the project's agent and contributor instructions (`AGENTS.md`, `CLAUDE.md`,
   `CONTRIBUTING.md`, the README's development section). They win over these skills where they conflict, except
   for the guard rails in `cpipe:run`. Read the learnings and the project's lessons too (`cpipe:run`, Learning:
   the version check, then Reading), once: each one is a check or a question this run must answer.
2. **Code.** If you have a code index or code-navigation tool, locate the code with it first. Read the code the
   task touches and the helpers you could reuse. Find the closest existing code that does the same kind of thing
   (a sibling route, component, job or test) and note how it handles errors, logging, data access, validation
   and naming: the build follows it. Skip code that is not on a path below.
3. **Flow map.** Trace every path through the code you will change, end to end, so nothing breaks out of sight:
   - **Entrances:** everything that reaches it: routes and endpoints, pages and UI actions, jobs, event and queue
     handlers, CLI commands, other modules and packages. Search for every usage across the whole repository, not
     only the direct callers.
   - **Exits:** everything it produces and who consumes it: responses and their shape, data written, events
     emitted, UI rendered, and the code on the other side that reads them (the client of a server change, the
     server of a client change, other services, shared types, generated clients).
   - **Guards on the way:** authentication, permissions and roles, tenancy, feature flags, validation, rate
     limits. Note which role or state may do what, on both sides (the server's check and what the client shows
     or hides).
   - **Behavior today:** a table of what happens now, read from the code on both sides of each boundary, not
     from the request. One row per actor and case: each role, the owner and a stranger, signed out, a job or
     another service, another client or page; new data and the older shapes still stored. Each row has the path
     and today's result (allowed or rejected, shown or hidden, the response and what the caller does with it),
     with file and line. A rule that lives on one side only (a permission the client enforces by hiding an
     action, validation only in a form, a limit one job applies) is today's policy: record its exact
     conditions, special cases included, and make each caller that does not pass through that side a row too.
   - **Why it is there:** for each check, condition or odd-looking line the change removes, tightens or moves,
     the reason it exists (`git blame -L <lines> <file>`, then that commit's message and pull request), and who
     relies on it. Recent fixes and reverts in these files (`git log --oneline -20 -- <files>`) are risks:
     regressions return where a bug was fixed before.
   Record the map with paths and line numbers. A path you cannot trace is a risk in the plan.
4. **Premortem.** Imagine the change shipped and something broke that the task never mentioned. List the most
   likely ways, looking through each lens that applies:
   - **Contracts:** API, schema, types, config, file formats, URLs, and anything a caller can observe: status
     codes, error text it matches, a field missing or null, order, defaults. Old clients, old data and old config
     still work; during a rollout the old client meets the new server and the reverse; after a rollback the old
     code still reads what the new code wrote. A contract changes by adding (the new beside the old, callers
     moved, the old removed later) unless the request asks to break it.
   - **Data and state:** existing records as stored, not as the types say (ids, dates and time zones, numbers,
     nulls, older values and shapes), migrations, caches and sessions holding old results, concurrent or
     repeated runs, partial writes.
   - **Who and where:** each role, tenant, locale, time zone, platform, device, feature-flag state, environment.
   - **Failure paths:** errors, timeouts, retries, empty and huge input, a dependency down, and a new error a
     caller handles wrongly (a denial that signs the user out, a retry that loops, a crash instead of a message).
     A success status whose body is empty, partial or for another record. A step after a write that throws, so
     the write is done but the caller is told it failed, and a retry writes again.
   - **User-facing:** loading, empty and error states, navigation, accessibility, wording.
   - **Sequences:** what the user does next, through the real screen or API: retry after a failure, submit twice,
     cancel then open again, open another record in the same screen or dialog, go back, refresh, a second tab.
     State kept from one attempt (ids, uploads, flags, drafts, a cached response) belongs to that attempt and
     that record: walk each sequence and say where it is kept, cleared or reused.
   - **Concurrency:** two requests, jobs, workers or tabs at once: check-then-write, read-modify-write, double
     submit, a retry that repeats a side effect, ordering between async steps, a missing lock, transaction,
     unique constraint or idempotency key, shared mutable state across requests.
   - **Resources:** each connection, pool client, transaction, file, stream, listener, timer, subscription or
     child process the change opens is closed on every path, errors included; pool size and timeouts under load;
     caches, maps, queues and retries have a bound; nothing grows per request or per render without a limit.
   - **Performance:** queries in loops (N+1), a new query without the index it needs, unbounded results without
     pagination or a limit, blocking or synchronous work on a hot path, work repeated on every request or render
     that could run once, payload size, the cost as the input or the data grows.
   - **The learnings and the project's lessons**, the areas of past misses, and anything its instructions warn
     about.
   Each risk gets a check (a test, a command or a QA step), or a one-line reason it cannot happen here, citing
   the code. These are prompts, not a form: find the risks this change has, including ones no lens names.
5. **Checks.** From set up's commands, pick the narrowest one that covers the change (one file, a filter), plus
   the build or typecheck, plus the broader suite for QA.
6. **Wait for the dependencies** before the first command that runs code in the worktree (a reproducing test):
   set up installs them in the background and writes `<run dir>/logs/install.done` when they are in. When it is
   not there yet, wait once: `for i in $(seq 60); do [ -f <run dir>/logs/install.done ] && break; sleep 15; done`
   (run it again if your terminal cuts it off; after 15 minutes, note it and go on). Never wait for the
   baseline: it runs in its own worktree of the base, so writing in this one does not touch it.
7. **Kind.** bug, feature, refactor, chore, port, hotfix or review (`cpipe:run`). Load the playbook. If the branch
   type from set up does not match the kind, rename the branch now.
8. **Reproduce** (bug and hotfix). Before planning, prove the cause: write the failing test in the worktree (the
   playbook's steps 1 to 3), run it, and record the RED output and the cause it proves. The plan
   is built on that cause, not a guess; a cause you cannot prove yet is a risk in the plan with the check that
   will prove it.

## 4. Decide the open questions

Never ask the user (`cpipe:run`, Unattended). List what the request leaves open: a product decision, which of
two behaviors is wanted, which base or tenant, an external system you have no access to. A question one of the
user's instructions answers is not open: apply the instruction. Settle each of the others from the
request, the code, the project's history and the conversation: pick the most likely answer, and of two equally
likely ones the more reversible. Each becomes an assumption in the plan with a one-line reason. Something only
the user may do (credentials, destructive actions, an unasked push) is left out of the scope and goes in the
report.

A fix keeps every feature working. When a risky path has to change, guard it (a safe client, limits, validation,
an allow list) and keep what the user sees; turning a feature off or hiding it is a step only when the user asked
for it. Of two ways to fix, never pick the smaller one because it drops a feature.

## 5. Plan

A plan profile writes the plan, and the builder (or you, without a build profile) builds from it, correcting what
the code contradicts. With a build profile and no plan profile, the builder writes the plan and builds from it in
the same brief. Write the plan to `plan.md` beside the run log (the run log names it). It is the builder's
instructions and is resent with every build reply, so it holds facts in this format and nothing else: at most
12,000 characters (`wc -c`), code cited by path and line and never quoted, no sections the format does not list.

- **Goal:** one sentence, in the user's words.
- **Kind** and playbook.
- **Base** (branch and commit) and **worktree**.
- **Scope:** the exact files to change, new ones marked `(new)`. **Non-goals:** what you will not touch.
- **Flow map:** the entrances, exits and guards from explore, with paths and line numbers.
- **Behavior change:** the behavior-today table with an After column. A row changes only when the goal or one of
  the user's instructions asks for it; name which. Every other row keeps today's result. A rule the change puts
  where it did not live before (a server check for what only the client enforced) copies today's conditions from
  the side that held it, so only the rows the request names change. Where the request's picture of today
  differs from the code, the code wins and the difference is an assumption.
- **Approach:** the existing code to follow (path), then each idea the change rests on, with why, the alternative
  avoided and the problem it would have caused, and the trade-off. The report's Approach table is built from this
  and the assumptions.
- **Acceptance:** numbered `A1`, `A2`…, each one rule in the form `When <trigger, state or input>, <who or
  what> <observable result>`, including the design note's states. Each edge the premortem keeps (an empty or
  wrong response, a failure after the write, a retry, a cancel and reopen) is a rule of its own, never a clause
  of another, so each gets its own test.
- **Must not change:** numbered `M1`…; for each row of the behavior table that keeps today's result, each
  entrance, consumer and guard in the map, and each behavior the user's instructions say to keep, the behavior
  that has to stay, with the check, test or QA step that proves it. An
  existing test or check that already covers it is named, not duplicated.
- **Risks:** numbered `R1`…; the premortem, each with its check or the reason it cannot happen. A new test only
  where nothing existing would catch the break.
- **Tests:** the new tests, each with the break it catches: the line or behavior that, when broken, makes it
  fail. Before adding one, look for an existing test that already fails on that break; then it covers the item
  and nothing is added. Extend an existing test before writing a new file. A test that fails on no realistic
  break of this change is not planned (rendering a constant, a getter, the framework, a mock returning what it
  was told). Fewer, sharper tests over many. Cases come from the behavior table: one per class of input or actor
  that gets a different result, plus the values at each boundary. A table with many rows (a permission matrix)
  becomes one table-driven test written first with today's results, passing on the base; the build then edits
  only the changed rows to their After results, so the test's diff is the behavior change. A test of code that
  crosses a boundary (a database query, an HTTP call, auth middleware) goes through the real layer or the
  project's in-memory version of it; a mock of the code under change proves nothing. A test reaches the change
  from an entrance in the flow map, the way the user or caller does (the UI action, the route, the job), in the
  order the user's sequence takes: never internal calls in an order no user can produce. A parser, validator,
  serializer, converter or other code with a wide input space also gets one property test (a round trip, an
  invariant, the same result as today on inputs the change must not touch) when the project already has a
  property-based testing library; never install one.
- **Checks:** the exact commands, with the baseline result of each (or `running` while it is not back).
- **Steps:** the build, in the playbook's order, one behavior per step, written so a builder who has not read
  the code can follow it:
  ```
  S1  <path>  <function, class or line it changes>
      Do: <add | change | remove | move | extract> <exactly what: names, values, conditions>
      Test first: <test file> <test name>, fails now because <reason>; or covered by <existing test>
      Verify: <narrow command from Checks> -> <expected result>
      Covers: A1, M2
  ```
- **Assumptions:** each with its one-line reason from step 4.
- **Deliverable:** a local commit on the delivery branch (default), or a draft pull request if asked.

### Plan gate

Before building, check the plan item by item and fix every no. It is your own check, never a stop:

- Every step's path exists (`git ls-files <path>`) or is marked `(new)`, and every function or class it names is
  found in that file (`grep -n`).
- Every Do line says exactly what changes. None says "as needed", "if needed", "make sure", "improve", "clean
  up", "etc.", "appropriately", "TBD", or leaves a choice open: decisions belong in step 4.
- Every step has a Verify command from Checks with its expected result.
- Every `A`, `M` and `R` is in some step's Covers, or names the existing test or check that covers it (a risk
  may instead give the reason it cannot happen).
- Every Scope file is in a step, and every step's file is in Scope.
- Every row of the behavior table either changes with the request's words that ask for it, or is held by an
  `M`. Every actor and caller that reaches a changed path has a row, not only the one the task named.
- No step leaves a file over 4,500 tokens (its size now, `wc -c` / 4, plus what the step adds) except by the
  import and call into a new file: the new code goes in a new file the step names (`steps/verify.md`, File size).
- Each step depends only on steps before it.
- Every acceptance rule has the `When … <result>` form and is covered by a test that starts at an entrance.
- The plan is at most 12,000 characters and has only the sections above. Over it, cut prose, quoted code and
  repeats, and merge steps that change one function, until it fits.

**Second opinion.** With a plan profile and a review profile, a plan of 6 steps or more, or one that touches
authentication, permissions, payments, deletion, migrations or the shape of stored data, gets one read by the
review profile before the build (`roles.md` in `cpipe:run`, Plan review). It is never a stop: its findings go
into the build brief, and the builder corrects the plan where one holds, as for anything the code contradicts.

Post the plan's goal and step count in one line together with your next tool call and start building; do not
wait for approval.
If the user steers mid-run, revise `plan.md`, note why in the run log and continue. If the user cancels, remove the
worktree and branch you created (nothing is committed on them) and the base worktree.

## Rules

- Nothing outside the worktree changes in these steps; exploring is read-only.
- The plan is the builder's instructions: exact enough to follow step by step without re-reading the code.
- Every acceptance criterion must be checkable by a test, a command or a QA step.
- Every new test must name the break it catches (Tests above); no test is added for coverage alone.
- Never ask the user anything; every open question ends as a recorded assumption.

## Done when

The flow map and the premortem are recorded, and the plan with its must-not-change list, risks, assumptions and
steps passes the plan gate and is in `plan.md`.
