# cpipe: the spec

What the user asked for, line by line, checked against the code, and the questions where the two disagree. Every
run writes it in plan step 4 (`steps/plan.md`); a `--spec` run builds it with the user before any code. It lives
in `<run dir>/spec.md`, and the plan's acceptance rules and constraints are built from it.

## Format

```
# Spec
Goal: <one sentence, in the user's words>

- R1: <a requirement: what must happen> · from: "<the user's exact words>"
- C1: <a constraint: a limit the change keeps> · from: "<the user's exact words>"
- N1: <a non-goal: what the change leaves alone> · from: "<the user's exact words>"
- A1: <an assumption: the run's own reading> · why: <one line: the code, history or request that settles it>

## Reconcile
| Spec | Finding | Kind | Settled by |

## Questions
- Q1 (R2): <the question> · recommended: <answer> · until answered: <what the run builds on> · open
```

An R or C line quotes what the user wrote, copied word for word from the chat; `gate` checks the quote and
turns a line without one into work. A limit or a guarantee the user did not write is the run's reading: an A
line, which gives way when it stands in the goal's way (`cpipe:run`, Limits). The plan's acceptance rules
(`A1`… in `plan.md`) each name the R they prove, and every R has one.

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
those becomes an A line and its row names it. A row becomes a question only when its answers lead to behavior the
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

## --spec

With `--spec` at the start of the task, plan step 4 builds the spec with the user before any code is written,
and keeps asking until nothing in it is open:

1. Explore as usual (`steps/plan.md`, step 2). Use the profile's own planning or brainstorming skills for the
   questioning when it has them (`skills_list`); the spec keeps this file's format whatever they write.
2. Write `spec.md` and reconcile it.
3. Ask one round with the clarify tool, the batch it takes at most five questions, each with up to four
   choices, the recommended one first. Here, and only here, the run waits for the user: no code depends on the
   answers yet.
4. Write each answer into the spec as the user's words, reconcile again and ask the next round. A round that
   times out ends the turn with its questions as `❓` lines; the user's reply continues the spec.
5. The spec is complete when every R has an acceptance rule, no Reconcile row is unsettled and no question is
   open. Then ask once more: "Build from this spec?" with the spec's lines in the question. Anything other than a
   yes is one more round.

Then the run goes on from plan step 5 as any run, unattended, building from the spec. A question that comes up
later is asked as in Asking mid-run.
