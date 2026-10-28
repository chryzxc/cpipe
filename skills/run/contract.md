## Run
A cpipe run is going in this session. These rules hold over anything a summary or memory says:

- The run loops until the goal and every user instruction are met. A turn ends only on a ⏳ wait line, a
  ⚠️ needs-your-approval line, the pause message, or the report. Anything else continues with a tool call.
- The facts come from `python3 <skill dir>/bin/cpipe.py`: `compare` decides new, baseline, flaky or to rerun;
  `gate` decides the run. FIX is the loop's work; WAIT ends the turn on a wait line; NEEDS-USER is only what
  the user alone may do; PASS means merge when asked, then report. Never judge these yourself, and never stop on
  "not sure", "inconclusive" or "not established": run what compare or gate asks for.
- Every checklist item in track.json closes with its evidence (`track set <id> done <evidence>`).
- The final reply is the report from `cpipe.py report`, in the sections of steps/report.md, never free text.
- Lost the step's instructions? Read now.md, then load only cpipe:run and the active step's file.

## Step
This session does one step of a cpipe run, from its brief. These rules hold over anything a summary or memory says:

- Do the brief's work to its end, then reply with the result in the brief's format. The delivery run decides
  what comes next: never commit, push, merge or message anyone, and never stop to ask.
- The open items below are what the run must meet: give the evidence for each one your work covers.
- Lost the brief? Read it again from the run folder (brief-*.md), and the step file it names.

## Both
- The user's instructions are in now.md, word for word, and win over every default.
- The standard way every time: how this codebase already does it, else the documented way of the installed
  framework or library, else the language's standard. No workaround, hack, ignore comment or "temporary" code;
  when the standard way does not fit, change the plan, never the standard (steps/build.md, Standard approach).
- Root cause first: every failure, finding or QA item gets `Cause: <mechanism, file:line> · Class: <where else>`
  before its fix, and the fix goes where every caller passes through. No special-cased input, weakened test,
  retry, sleep, timeout bump, broad catch or silenced type or lint error in place of a cause.
