# cpipe: continue and restart

Part of `cpipe:run`, loaded on a continue, a restart or a chat restarted mid-run. The wait line,
Facts and the run log are in `SKILL.md`.

## Continue

When the user says "continue" (or `--continue`), a notification arrives mid-run, or the chat restarted mid-run: read `now.md`, run
`active <run dir>`, `track <run dir> open` and `gate <run dir>` (Facts), read the run log only where they point, check `git status` and the last
check results in the worktree, and pick up at the first unfinished step. Do not redo finished steps. A run started
before these files existed is brought up to them first: `now.md` gets its `## Instructions` section with the user's
instructions word for word, `track.json` the Done when of each step left and every instruction, and `results/` the
base and delivery results of each check, so the gate decides from facts. Sometimes a report left an instruction
unmet, and then the user says "continue" or repeats the instruction. That is the same task: do what is unmet, never
only report again that it is not done. A "continue" after an approval this chat asked, refused or timed out, is
the user's answer to it: run that command again here, so the approval asks again, and go on from there; never
hand it to a role step, which cannot ask. A restart ends this chat's
turn, but not the profiles and checks it started: before starting one again, check its process (the pid in `now.md`)
and its log or reply file, and wait for it or use its result. A "continue" while what the run waits on still runs:
do any step that does not need its result, else end the turn with the wait line (`SKILL.md`, Waiting ends the turn) and ` · <m> min in` after the
step; never read its partial output or start it again. With several deliveries, finish the unfinished one first,
then start the next one its run log names.

**Restart.** `--restart [<step>]` redoes the run from that step (Explore when none is named), on the
roles this run sets and with new role sessions, whoever did it before. Before a restart at Explore through Build,
save the worktree's changes from the base as `<run dir>/restart-<n>.patch`, then put the worktree back on the base
(reset, and remove the files the run added). Mark that step and every later one unfinished in `now.md` and
`track.json`, write `Restart: from <step> · <the user's reason>` in the run log, and go on from that step. The
steps before it keep their results; a restart at Verify or later keeps the code.
