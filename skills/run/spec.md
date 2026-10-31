# cpipe: the spec

What the user asked for, line by line, checked against the code, and the questions where the two disagree. Every
run writes it in plan step 4 (`steps/plan.md`), from the request and any planning the user did before the run
(Planned before the run), or with the user first on the plan profile with `--spec`. It lives in
`<run dir>/spec.md`; the plan is built from it and the delivery proves it line by line.

## Format

```
# Spec
Goal: <one sentence, in the user's words>

- R1: <a requirement: what must happen> · from: "<the user's exact words>"
- C1: <a constraint: a limit the change keeps> · from: "<the user's exact words>"
- D1: <a design decision the user agreed to> · from: <the plan file, section, or "<their words agreeing>">
- N1: <a non-goal: what the change leaves alone> · from: "<the user's exact words>"
- I1: <an inference: the run's own reading> · why: <one line: the code, history or request that settles it>

## Reconcile
| Spec | Finding | Kind | Settled by |

## Questions
- Q1 (R2): <the question> · recommended: <answer> · until answered: <what the run builds on> · open
```

An R or C line quotes what the user wrote, copied word for word from the chat; `gate` checks the quote and
turns a line without one into work. A limit or a guarantee the user did not write is the run's reading: an I
line, which gives way when it stands in the goal's way (`cpipe:run`, Limits). The plan's acceptance rules
(`A1`… in `plan.md`) each name the R they prove, and every R has one (Proven line by line).

## Reconcile

Read each R, C and N against what explore found and record every mismatch as a row, its finding cited by file
and line:

- **Today:** the request describes the code differently from what it does.
- **Conflict:** two of the user's lines ask for results that cannot both hold.
- **Two readings:** one line can mean two things that behave differently.
- **Infeasible as written:** a line cannot be met under another as the user wrote it.
- **Goal vs. non-goal:** the goal needs a change an N rules out.

Most rows are settled without the user: by the code (the code wins over the request's picture of today), by
another of the user's lines, by the project's history, or by the most likely and reversible reading. Each of
those becomes an I line and its row names it. A row becomes a question only when its answers lead to behavior the
user would notice and nothing they wrote or the code says which. Never a question about the scope, which fix,
how big the change may be or how hard to try: those are the run's (the user's standing answer in the run
message). What only the user may do (credentials, destructive actions, what leaves this machine) is a `user`
item of its kind, not a question.

## Asking mid-run

A question never stops the work:

1. Add it under Questions with its recommended answer, and to the checklist:
   `track <run dir> add <step> "<the question>"`, then
   `track <run dir> set <id> user "answer: R2: <the question> · recommended: <answer>"`. `track` refuses an
   `answer:` item that names no R or C line in `spec.md`.
2. Post it in one line with the run's next tool call, up to five questions in one line each:
   `❓ <n>/11 <Step> · R2: <the question> · going with <recommended> until you answer`.
3. Keep working on the recommended answer. Work that a different answer would undo goes last.

Never call a clarify or question tool mid-run: it halts the whole run until the user answers.

**The answer** arrives as a message mid-run, steering like any other (`cpipe:run`, Unattended). Rewrite the line
it settles with their words as its `from:`, mark the question `answered: <their words>`, close the item
(`track set <id> done "answered: <their words>"`), and when it differs from the recommended answer, revise
`plan.md` and redo what it changes. A reply that does not answer leaves the question open.

**Nothing left but answers.** When `gate` says `ASK`, every other item is done and the run waits on the user: end
the turn with one line per open question,
`❓ <n>/11 <Step> · waiting on your answer · R2: <the question> · recommended: <answer>`,
and no report and no next step. The answer starts the next turn: close it, finish what it changes, run `gate`
again and go on. A `FIX` or `WAIT` is never ended on a question line: that turn is still the loop's.

## Planned before the run

The user may plan in the chat before starting cpipe: talking it through, or the host's plan mode writing a plan
file (Hermes: under the workspace's `.hermes/plans/`). Read that first. It is the spec's source, not the
builder's plan:

- **The user's answers and decisions** in that conversation become R, C and N lines, quoted word for word.
- **A plan file or design the user agreed to** becomes D lines, one per decision it makes about how the change
  works (where it lives, the data it keeps, the flow, what each role sees), each
  `· from: <the plan file, section>` or `· from: "<the user's words agreeing>"`. A D line binds like the user's
  words: the planner builds on it, and where the code contradicts it, that is a Reconcile row and a question.
- **A question that conversation already answered** is never asked again.

The planner still runs (`steps/plan.md`, step 5, on the plan profile): a spec says what to build and how it
works, often not completely, and the plan fills in what the builder needs, from the code, the standard approach
and the steps. Where the plan adds a decision of its own it is an I line; where it would change an R, C or D line
it is a question.

## Proven line by line

Every R, C and D line is a checklist item whose text starts with its id: `track <run dir> add plan "R1: <the
line>"`. `gate` stays at `FIX` while a line has no item. The plan's acceptance rules and tests name the lines
they prove (`Covers: R1, A2`, `A2` an acceptance rule of the plan), QA checks each one the way the user would,
and the item closes with that proof (`track set <id> done "<the test or QA step and its result>"`). `report`
lists each line with its proof under Proof. An answer that changes a line reopens its item: the proof is redone for the new line.

## --spec

With `--spec` at the start of the task, the spec is built with the user before any code, by the plan profile: its
bot and model do the thinking, whatever model this chat runs on. This chat only carries the questions and
answers. Without a plan profile, the profile that plans in its place (`roles.md`) does it.

1. **Set up and explore** as any run (`steps/setup.md`, `steps/plan.md` step 2), explore on the plan profile.
2. **Spec round.** Hand the plan profile a brief (`roles.md`, Handing a step to a role) that asks for `spec.md`
   in this file's format, reconciled with the code, and its open questions: at most five, each with up to four
   choices, the recommended one first, each naming the line it settles. It uses its own planning or
   brainstorming skills for the questioning when it has them. It writes no plan and no code.
3. **Ask.** Put its questions to the user with the clarify tool, one round. Here, and only here, the run waits
   for the user: no code depends on the answers yet. A round that times out ends the turn with its questions as
   `❓ 4/11 Spec · <question> · recommended: <answer>` lines; the user's reply continues the round.
4. **Next round.** Resume the plan profile's session with the answers in the user's words: it writes them into
   the spec, reconciles again and returns the next questions. Loop from 3.
5. **Complete** when the plan profile returns no question, every R has a line it can be proven by, and no
   Reconcile row is unsettled. Then ask once with the clarify tool: "Build from this spec?" with the spec's lines
   in the question; anything but a yes is one more round.

Then the run goes on unattended from plan step 5, the plan written in the same plan profile session, and builds
and proves the spec line by line. A question later in the run is asked as in Asking mid-run.
