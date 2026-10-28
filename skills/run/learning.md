# cpipe: learning

Part of `cpipe:run`. Load it when explore reads the learnings (`steps/plan.md`), when a miss is found
(QA, review, the user's feedback) and at the report.

cpipe learns from every miss, so each user's install gets better at their work. Nothing here is ever committed
or shared.

**Where it lives.** Under the Hermes root (`cpipe:run`, Roles), `cpipe/`:
- `history.jsonl`: one line per finished run (`{"type": "run", "date", "project", "kind", "steps": [the steps that
  ran]}`) and one per miss (`{"type": "miss", "date", "project", "kind", "area", "severity", "found_by": "user" |
  "qa" | "review" | "verify", "step": the step that should have caught it, "why"}`). Append only.
- `learnings/<step>.md` for `plan`, `design`, `build`, `qa` and `review`, and `learnings/general.md` for how the
  user wants work done in any project (when to push, branch and pull-request format, naming). An older
  `cpipe/lessons.md` counts as `general.md`.
- `learnings/retired.md`: learnings taken out of use, each with its reason, so the user can see and restore them.

A profile's own `<Hermes root>/profiles/<name>/cpipe/learnings/<step>.md` adds to these for steps that profile
does, for what is specific to it. The project's lessons live at
`$(git rev-parse --git-common-dir)/cpipe/lessons.md`, shared by its worktrees: facts about that code (where to
look, what breaks together).

**What a learning is.** One line that adds to cpipe's rules: something to check, where to look, a state to cover,
a question for the premortem, how the user wants a thing done. It is written as a rule, not a story, with why,
the cpipe version it was written under, how many runs it applied to and when it last did:
`- QA covers the empty state of every list a change touches: two lists shipped broken empty · 3.55.0 ·
applied 2 · last 2026-10-27`. Never secrets, customer data or code.

**Core rules come first.** A learning only ever adds. It never skips or reorders a step, loosens a check, a gate or
a limit, or contradicts what cpipe's files say, and the guard rails always hold. When one does, cpipe's file wins
and the learning is retired with `conflicts with core`. So updating cpipe never breaks on old learnings: the first
run after an update (a learning's version older than this skill's) checks each older learning against the current
step file once: one the file now covers is retired with `now in core`, one that conflicts is retired with `conflicts
with core`, the rest get the new version. The order, highest first: the user's instructions for this task, the
user's Hermes setup (`cpipe:run`, The user's own skills come first), cpipe's guard rails and step rules, then the
learnings (profile, then step and general, then the project's), adding detail on top.

**Reading.** Explore reads all of them once, after the version check: each learning that fits this change goes
into the plan as a check, a QA item, a premortem question or a review question, so whoever does the step gets
it from the plan and its brief. Count it as applied (`applied <n+1> · last <today>`). From `history.jsonl`,
take the last 20 runs of this kind: a step with a miss in them (a `found_by: user` miss weighs most) gets its
misses' areas checked by name in this plan.

**Writing.** Write a learning, and a miss in `history.jsonl`, whenever something was caught later than it should
have been: a review or QA finding the plan did not foresee, a check that failed for a reason explore could have
seen, and above all a problem the user found after the report. File it under the step that should have caught it;
a correction about how the user works goes in `general.md`. Merge it into a matching learning rather than adding a
near-duplicate; when a miss repeats in the area of an existing learning, that learning did not work: rewrite it
sharper. At most 30 lines per file: retire the least applied first. A learning not applied in the last 90 days is
retired with `unused`. At the report, append the run's line to `history.jsonl`.

**Confidence.** The report's Confidence line adds the measured record when `history.jsonl` has at least 5 runs of
this kind: `Past: QA missed 1 of 20 UI changes you checked`, counting `found_by: user` misses per step.

**Feedback.** When the user reports problems after the report (`/cpipe feedback <what they found>` or a plain
message), find the run (this chat's, else the newest run log of the worktree or branch it names), reproduce each
first. One that is real and inside the task's scope is a miss: record it with `found_by: user` and the step that
should have caught it, write or sharpen the learning, then fix it as a follow-up (`cpipe:run`, After the report).
A new request or a change of mind is not a miss.

**Showing them.** `/cpipe learnings`: list each file's learnings, the retired ones, and per step the misses that
reached the user in the last 20 runs of each kind. Do not start a run.
