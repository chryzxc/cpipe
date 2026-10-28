# cpipe: deliveries in parallel (`--parallel`)

When the task message starts with `--parallel`, each issue (`cpipe:run`, One issue, one delivery) is delivered in
a session of its own, a lane, running in the background. This chat is the dispatcher: it starts the lanes, posts
each lane's report as it arrives and writes the summary. It never runs a step, reads a lane's code, plan or
worktree, or waits on anything but the lanes. One issue in a `--parallel` task is delivered in this chat as usual.

## In this chat

1. **Batch.** Split the task into its issues. The batch folder is `<Hermes root>/cpipe/lanes/<YYYYMMDD-HHMMSS>/`.
   Write its `now.md`: `## Instructions` with the user's instructions word for word (Run log), then `## Lanes`
   with each lane's number and issue. Register it: `active <batch dir>` (`cpipe:run`, Facts).
2. **Lanes.** One folder per issue, `<batch dir>/<n>/`, numbered from 1, each with an empty `inbox.md` and a
   `brief.md` that starts with `# <the issue in one line>` and then says, in this order:
   - Load the skill cpipe:run with skill_view (without skill_view, read `<skill dir>/SKILL.md` with your file
     tool) and deliver this one issue as a lane of a parallel batch: follow `parallel.md` beside it, In a lane.
     The Hermes root: `<root>`. Lane folder: `<lane dir>`. Run it unattended to the report: this is my last
     message, so never stop to ask or wait.
   - The issue in full: its number or link and everything the user said about it.
   - The user's instructions, word for word, every one that applies to all issues (push, pull request, merge).
   - The other lanes: `Lane <n> of <total>; others: <their issues>`.
3. **Start.** Who runs a lane is this chat's own profile and model: the command `chat` prints (Facts). The time
   limit is 14400 seconds unless the user gave one. `lanes <batch dir>` prints the lanes and what to do
   (`START <n>`); run every start in one reply, each in the background with a completion notification and a
   heartbeat of 300 seconds (the lane prints a `LANE` line each time its step changes; a quiet tick is skipped):
   `sh <skill dir>/bin/lane.sh <batch dir>/<n> <repository> <limit> <who>`, the repository being the checkout the
   task came from: a session started in it is filed by Hermes under that repository's project, and the launcher
   saves it as a chat (role sessions stay hidden), so every lane shows in the session list there. Up to four
   run at once (`--at-once <n>` when the user names another number); the rest are queued and start as lanes
   finish.
4. **Wait.** End the turn with `⏳ <running> lanes running, <queued> queued · each reports here · resumes on its
   own`. A lane's notification starts the next turn.

**A notification.** It holds `lanes <batch dir>` as the lane printed it when it ended: one line per lane, what to
do, and the ended lane's report. A lane `taken over in its chat` is the user's: never start it again. Post
that report as it is, under `Lane <n> · <issue>`. Then act on each verdict in the same reply: `START <n>` starts a queued lane; `RESUME <n> [-w <s>] [--resume <id>]` starts that lane again
as in Start, with those options before the lane folder (`sh <skill dir>/bin/lane.sh -w <s> <lane dir> …
--resume <id>`), so a lane that stopped without its report (the turn limit, the time limit, a crash) goes on where it was; `WAIT` ends the
turn on the wait line. Run `lanes <batch dir>` again before acting when the notification is not the newest
thing in the chat. `DONE` writes the summary.

**A heartbeat.** It holds the `LANE <n> · <issue> · <step>` lines a running lane printed since the last one.
Post them as they are, one line each, and end the turn on the wait line; nothing else runs.

**A message from the user.** Steering for one lane (it names its issue or number) is appended to that lane's
`inbox.md`, for every lane to every `inbox.md` and to the queued lanes' `brief.md`; add it to the batch's
`now.md` too. A question about progress is answered from `lanes <batch dir>`. The user taking a lane over (to
continue or restart it in its own chat): write `user` to `<lane dir>/taken` first, then stop the lane with your
process tool's kill and any role step it left running (`pkill -f <its run dir>/logs/`).
Then end the turn on the wait line.

**Continue** (or a restart): `active <batch dir>`, then `lanes <batch dir>` and act on its verdicts: a lane whose
process is gone is resumed, so a notification that never came loses nothing.

**Summary.** When `lanes` prints `DONE`: one status line for the batch, `✅ Done: <n> deliveries` or
`⚠️ Done except: <what is left, per lane>`, then one line per lane, `<n> <issue> · <its status line> · <its pull
request or branch>`, then `active <batch dir> --done`. The lanes' own reports are already in the chat.

## In a lane

The brief names a lane folder: this session delivers that one issue through the whole flow of `cpipe:run`, with
these differences.

- **Register.** Once the run log exists, write the run folder's path to `<lane dir>/run`: the dispatcher reads the
  current step from its `now.md`, and the run's rules stay pinned in this session from it.
- **Wait on each step.** This session ends when a turn ends, and no notification wakes it, and Hermes cuts any one
  tool call off after a few minutes, a foreground command included, while the command itself goes on. So run each
  role step (`roles.md`, Run) and other long work (installs, the baseline) in the background, and wait on it with
  your process tool's wait, 300 seconds at a time, until it exits. A wait that ends with the process still running,
  or that Hermes cut off, means wait again: the step is still working, so never start it again, hand its work to
  another role or go on without its result. It ends on its own at its time limit. No ⏳ wait lines and no progress
  tick: a turn ends only on the report or the pause message.
- **Steering.** At the start of each step, read `<lane dir>/inbox.md`. A line not yet in `now.md` is the user's
  steering (`cpipe:run`, Unattended): add it to `now.md` and act on it.
- **Approvals.** A command that needs an approval takes Hermes' path for one-shot sessions. One that is refused is
  the user's to give: finish the rest and name it in the report's `⚠️ Done except` line.
- **Only this issue.** Never touch another lane's branch, worktree or pull request. Before a merge, update this
  branch onto the base as it is then (`steps/report.md`, Merge): another lane may have merged first.
- **Taken over.** When `active` says this chat took the lane over (its launcher had stopped and the user
  continued it here), this is a chat like any other from then on: run and wait on role steps as `roles.md`, Run
  says, and write the report to `<lane dir>/report.md` as well as replying with it. The dispatcher leaves the lane
  to this chat and posts that file.
- **The report** is this session's final reply, in the sections of `steps/report.md`; the dispatcher posts it.
