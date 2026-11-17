# cpipe reference

## Facts

Questions with an exact answer are answered by `bin/cpipe.py` beside this file, never by judgment. Run it as
`python3 <skill dir>/bin/cpipe.py <command> …` from the worktree (`<skill dir>`: this file's folder, which the start
message names; `bin/` also holds the time limit, the progress tick and the role launcher); it reads files
and prints data and a verdict, and you act on what it prints:

- `results <run dir>/results/<check>.<tree>.json <junit|jest|tsc|eslint|lines> <files>`: a check's results from
  the runner's machine-readable output (`<tree>` is `base`, `head`, `base-alone` or `head-alone`), so a cut-off
  log never decides anything (`steps/verify.md`, Names from the runner).
- `compare <run dir> <check>`: every failing name on the delivery as **new**, **baseline**, **flaky** or a rerun
  to do (`rerun-head`: run it alone on the delivery; `rerun-base`: run it on the base). Never undecided: do the
  reruns and compare again.
- `track <run dir> add <step> <text>`, `set <id> implemented|done|not-verified|skip|user <evidence>`,
  `open [<step>]`: the checklist in
  `track.json`. When a step starts, add its Done when items, one per condition; each instruction in `now.md`
  the moment it arrives (step `instruction`, its words; `gate` holds until each is on); at the report each merge
  condition. An item closes only with its evidence (a result, a file, a count):
  `implemented` when code exists but outcome evidence is still owed (it stays open), `done` when the outcome is
  verified, `not-verified` only as `machine: <what this machine cannot run>` (reported as an exception, not a pass),
  `skip` only as `n/a: <why it does not exist in this change>` or legacy `machine: <what this machine
  cannot run>` and never for a user's instruction, `user` for what only the user may do, its evidence starting
  with its kind: `credentials:`, `destructive:`, `external:` (push, pull
  request, merge, deploy, messages) or `approval:` (one Hermes asked the user, refused or timed out), or a
  spec question `answer: <R or C id of spec.md>: <question>` (`spec.md`). `track` and
  `gate` refuse any other: which fix, the scope or how the code behaves is the run's to decide. Unclear, "not established" or "inconclusive" stays open and is work for the loop.
- `gate <run dir>`: the run's verdict. `FIX: …` is the loop's next work; `WAIT: …` ends the turn on a wait line;
  `SPEC: …` the spec is still drafted with the user (`spec.md`, --spec);
  `ASK: …` is only the user's answers left: end the turn on the `❓` waiting lines (`spec.md`);
  `NEEDS-USER: …` is what only the user may do; `PASS` means the checklist is settled, with any machine-only
  verification exceptions kept explicit in the report. It never proves absent CI green. Merge only when asked
  and its conditions hold, then report.
- `ci <run dir> <pull request>`: waits for the pull request's CI, saving `results/ci.json` on every read and
  reading through GitHub's passing errors; a `WAIT: CI …` turn ends only with it running (`steps/publish.md`).
- `report <run dir>`: the report's skeleton with its facts filled in; `report <run dir> --check`: `PASS` when
  `<run dir>/report.md` keeps those facts and has every section and cell written, else `FIX` with what is wrong
  (`steps/report.md`, Report).
- `cost <run dir> <worktree> --start`: capture private usage baselines before role work; later calls add only
  missing baselines. `cost <run dir> <worktree>` reads measured deltas per profile, model, provider and task,
  with coverage warnings. Without capture it labels legacy lifetime counters (`steps/report.md`, Cost).
- `active <run dir>` once the run log exists (`report <run dir> --check` closes it when it passes, and
  `active <run dir> --done` when the user ends the run, with the chats it was compacted from): registers this chat's
  run, so the run's rules, the user's instructions and the open items stay pinned in this chat's system
  prompt whatever a compaction drops, and `gate` and the watcher check the run. `/cpipe` marks the chat's run as
  started; until `active` registers its folder, every turn is told to set it up and register it, and nothing else
  counts. A `project:` line it prints names the user's project that holds the repository while this chat is in
  none: move this chat into it before anything else (`steps/setup.md`, This chat's project), on a continue too.
- `port <source> <base> [--repo <source repository>]`: each line the source change (a commit, or `<from>..<to>`)
  adds that this tree's diff from the base does not contain, by file, the code's before the tests'
  (`playbooks/port.md`).
- `lanes <batch dir> [--at-once <n>] [--report <lane>]`: each lane of a `--parallel` batch as queued, running,
  reported or stopped, then what to do: `START`, `RESUME`, `WAIT` or `DONE` (`parallel.md`).

Long command output goes to a file beside the run log (`logs/<step>-<check>.txt`), not into the chat:
`sh <skill dir>/bin/check.sh [-h] <seconds> <run dir>/logs/<step>-<check>.txt <command> [<arguments>]`.
It invokes the existing time limit, saves all output and the exit status, prints the tail and returns that status.
Never append a display pipeline that replaces it. Machine-readable runner results still decide by-name comparisons.
