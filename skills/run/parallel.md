# cpipe: deliveries in parallel (`--parallel`)

When the task message starts with `--parallel`, or the user asks for issues in parallel in their own words, each
issue (`cpipe:run`, One issue, one delivery) is delivered by a sub-agent of this chat, a lane (Hermes'
`delegate_task`): it runs in the background, shows inside this chat, where the user opens it to watch, and stays
out of the session list. This chat is the dispatcher and only a status monitor: it starts the lanes, posts each
lane's report as it arrives, and writes the summary. It never delivers an issue itself, one issue
included, never runs a step, reads a lane's code, plan or worktree, or waits on anything but the lanes.

A delivery this chat was already running when parallel is asked (the user asks for more issues alongside it, or
for parallel mid-run) becomes a lane too: its brief names its run folder, and the lane continues that run. From
then on this chat registers the batch, not the run, and never touches that run's worktree or role steps again.

## In this chat

1. **Batch.** Split the task into its issues. Find each issue's repository (usually one for all) as
   `steps/setup.md`, Set up, step 1 says, with its checks when this chat is not in it; it is the only step file
   the dispatcher reads, and a lane never looks for its repository again.
   Work this chat was doing on a delivery (a review, a fix, a port) is an issue of the batch, with its run folder.
   The batch folder is `<Hermes root>/cpipe/lanes/<YYYYMMDD-HHMMSS>/`.
   Write its `now.md`: `## Instructions` with the user's instructions word for word (Run log), then
   `## Lanes` with each lane's number, issue and repository (its top-level path). Register it:
   `active <batch dir>` (`cpipe:run`, Facts).
2. **Lanes.** One folder per issue, `<batch dir>/<n>/`, numbered from 1, each with an empty `inbox.md` and a
   `brief.md` that starts with `# <the issue in one line>` and then says, in this order:
   - Load the skill cpipe:run with skill_view (without skill_view, read `<skill dir>/SKILL.md` with your file
     tool) and deliver this one issue as a lane of a parallel batch: follow `parallel.md` beside it, In a lane.
     The Hermes root: `<root>`. Lane folder: `<lane dir>`. Repository: `<its repository>`. Run it
     unattended to the report: this is my last message, so never stop to ask or wait.
   - The issue in full: its number or link and everything the user said about it.
   - For a delivery this chat had started: `Run folder: <run dir>. Continue that run (continue.md): its finished
     steps stay done, and a role step it left running is waited on, never started again.`
   - The user's instructions, word for word, every one that applies to all issues (push, pull request, merge).
   - The other lanes: `Lane <n> of <total>; others: <their issues>`.
3. **Start.** `lanes <batch dir>` prints the lanes and what to do (`START <n>`), and a ⚠️ line first when the
   lanes would not run on this chat's model: post that line as it is. Start each lane with its own
   `delegate_task` call, all in one reply, one task each, so each lane's result comes back on its own. The goal's
   first line names the lane wherever Hermes lists it (its title, the sub-agent's row): `Lane <n> · <the issue in
   one line, under 60 characters>`, then on the next line `Deliver lane <n> of a cpipe --parallel batch: read
   <lane dir>/brief.md and follow it.` Give no profile
   unless the user named one: the lane runs on this chat's model, and its role steps on their own profiles.
   Hermes' delegation config sets a lane's limits (its turns and time) and how many run at once: up to four
   (`--at-once <n>` when the user names another number, never more than Hermes' `max_concurrent_children`,
   or Hermes runs the next one in the foreground and this chat waits on it); the rest are queued and start
   as lanes finish.
4. **Wait.** End the turn with `⏳ <running> lanes running, <queued> queued · each reports here · resumes on its
   own`. A lane's result starts the next turn.

**A lane's result.** Run `lanes <batch dir> --report <n>`: one line per lane, what to do, and the lane's report
(its run's `report.md`). Post that report, else the lane's final reply as Hermes returned it, under
`Lane <n> · <issue>`. A lane `taken over in its chat` is the user's: never start it again. Then act on each
verdict in the same reply: `START <n>` and `RESUME <n>` start that lane as in Start (a resumed lane finds its
run and goes on where it was: the turn limit, the time limit, a crash); `RESUME <n> after <s>` (a lane that
stopped within minutes) starts `sleep <s>` in the background with a completion notification, whose
notification runs `lanes` again; `WAIT` ends the turn on the wait line. Run `lanes <batch dir>` again before
acting when the result is not the newest thing in the chat. `DONE` writes the summary.

**A message from the user.** Steering for one lane (it names its issue or number) is appended to that lane's
`inbox.md` (`printf '%s\n' '<the message>' >> <file>`, never a rewrite of a file this chat has not read), for
every lane to every `inbox.md` and to the queued lanes' `brief.md`; add it to the batch's `now.md` too. The
user's answer to a lane's ❓ question goes to its `inbox.md` the same way, then `lanes` starts it again. A
question about progress is answered from `lanes <batch dir>`. The user taking a lane over (to continue or
restart its run in a chat of their own): stop the lane (`delegate_task` with action `list` for its id, then
action `stop`) and any role step it left running (`pkill -f <its run dir>/logs/`); the chat that registers its
run takes the lane over. Then end the turn on the wait line.

**Continue** (or a restart): `active <batch dir>`, then `lanes <batch dir>` and act on its verdicts. Hermes stops
every lane with this chat (`/stop`, `/new`, the app closing): each shows as stopped and is resumed, so a result
that never came loses nothing.

**Summary.** When `lanes` prints `DONE`: one status line for the batch, `✅ Done: <n> deliveries` or
`⚠️ Done except: <what is left, per lane>`, then one line per lane, `<n> <issue> · <its status line> · <its pull
request or branch>`, then `active <batch dir> --done`. The lanes' own reports are already in the chat.

## In a lane

The brief names a lane folder: this sub-agent delivers that one issue through the whole flow of `cpipe:run`,
with these differences.

- **Register.** Once the run log exists, write the run folder's path to `<lane dir>/run` and run
  `active <run dir>`: the dispatcher reads this session and the current step from them, and the run's rules stay
  pinned in this session. A lane folder that already has a `run` (the lane was started again), or a brief that
  names a run folder: write it there, run `active <run dir>` first, then continue that run (`continue.md`).
- **Wait on each step.** A sub-agent ends when its turn ends, and no notification wakes it. So run each role step
  (`roles.md`, Run) and other long work (installs, the baseline) in the background, then wait on the role steps
  with one foreground command: `sh <skill dir>/bin/wait.sh <run dir> 390 <run dir>/logs/<role>-<n>.txt ...`, every
  step you wait on, with a terminal timeout of 420 seconds (Hermes cuts any one tool call off at about 7 minutes).
  It returns once they have all exited, with each log's end and the round counts, or after 390 seconds with
  `still running: <log>`. Other long work you wait on with your process tool's wait. A wait that ends with a step
  still running, or that Hermes cut off, means wait again: the step is still working, so never start it again,
  hand its work to another role or go on without its result. It ends on its own at its time limit. No ⏳ wait
  lines and no progress tick: a turn ends only on the report or the pause message.
- **Steering.** At the start of each step, read `<lane dir>/inbox.md`. A line not yet in `now.md` is the user's
  steering (`cpipe:run`, Unattended): add it to `now.md` and act on it.
- **Approvals.** Hermes asks no one for a sub-agent and denies a command that needs an approval at once, so no
  approval ever reaches the user from here. A blocked command is done the standard way (`cpipe:run`,
  Unattended): file tools for edits, a script file, the program with its arguments; a role step's reply that one
  was blocked gets a follow-up saying that, never "run it again". Only an action that needs the user in itself
  (deleting their data, a forced push) and has no other way is left: finish the rest and name the exact command
  in the report's `⚠️ Done except` line, with `approve it by continuing this lane's run in a chat of your own`.
  Never the needs-your-approval line in a lane: a lane started again cannot ask either.
- **Only this issue.** Never touch another lane's branch, worktree or pull request. Before a merge, update this
  branch onto the base as it is then (`steps/publish.md`, Merge): another lane may have merged first.
- **Taken over.** When `active` says this chat took the lane over (the lane had stopped and the user
  continued it here), this is a chat like any other from then on: run and wait on role steps as `roles.md`, Run
  says. The dispatcher leaves the lane to this chat and posts the run's `report.md` (`steps/report.md`, Report).
- **The report** is this sub-agent's final reply, in the sections of `steps/report.md`; the dispatcher posts it.
