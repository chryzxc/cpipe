---
name: design
description: "cpipe: design the user-facing side before planning."
version: 3.3.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delivery, design, ux, accessibility]
    related_skills: [cpipe:run, cpipe:plan, cpipe:qa]
---

# cpipe: design

Step 3 of `cpipe:run`. Decides what the user will see or call before anything is built, so the plan has concrete
acceptance criteria and QA has something to check. If the roles file names a `design` profile, hand this step to
it (`roles.md` in `cpipe:run`) and check its note against the code. Otherwise do it yourself.

## When it applies

Any change a person sees or calls: a page or component, a form, a message or error text, an email, a CLI's output
or flags, an API's request or response shape. If the task changes none of these (an internal fix, a refactor, a
chore), write `Design: not needed (<reason>)` in the run log and go to step 4.

## Steps

1. **Start from what exists.** Find the closest existing screen, component, command or endpoint, and the
   project's design system, shared components, copy style and API conventions. Reuse them.
2. **Every state.** For each surface the task touches: default, loading, empty, error, success, disabled, and
   permission-denied where it applies. Say what the user sees in each.
3. **Flow.** The steps a user takes, where they start and end, what happens on cancel, retry and refresh.
4. **Copy.** The exact words of labels, messages and errors, in the product's existing tone.
5. **Accessibility.** Labels for inputs, keyboard use and focus order, contrast, meaning not carried by color
   alone.
6. **Shape.** Narrow and wide screens for UI; fields, types, status codes and errors for an API; flags, output
   and exit codes for a CLI.
7. **Edge cases.** Long text, many items, no items, slow or failed requests, two people acting at once.

## Output

A design note in the run log: the surfaces, each state, the copy, the edge cases. Each item becomes an
acceptance criterion in the plan. Product questions you cannot default go into the one clarify message in
step 4, each with your proposed answer.

## Rules

- Reuse the existing design system and patterns; a new pattern needs a written reason.
- Never change the behavior of an existing surface the task does not name.
- Never drop accessibility for looks.
- No code in this step: it is a note, not an implementation.

## Done when

The design note is in the run log with every state of every touched surface, or the step is marked not needed
with its reason.
