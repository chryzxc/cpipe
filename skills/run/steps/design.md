# cpipe: the design step

Step 3 of `cpipe:run`. Decides what the user will see or call before anything is built, so the plan has concrete
acceptance criteria and QA has something to check. A `design` profile in the roles file is used only for a new or
changed screen (a page, a view, a component, a form), before the planner or builder starts (`roles.md` in
`cpipe:run`): it finds the screens itself, and whoever plans corrects what the code contradicts. Every other shape
(a message, an email, a CLI's output or flags, an API's request or response) is designed by whoever plans: the
planner, else the builder, otherwise you.

## When it applies

Any change a person sees or calls: a page or component, a form, a message or error text, an email, a CLI's output
or flags, an API's request or response shape. If the task changes none of these (an internal fix, a refactor, a
chore), write `Design: not needed (<reason>)` in the run log and go to step 4.

## Steps

1. **Start from what exists.** Find the closest existing screen, component, command or endpoint, and the
   project's design system, shared components, copy style and API conventions. Reuse them. The new part goes where
   the spec's Where it shows line puts it, in that thing's format (`spec.md`), never in a section of its own.
2. **Every state.** For each surface the task touches: default, loading, empty, error, success, disabled, and
   permission-denied where it applies. Say what the user sees in each.
3. **Flow.** The steps a user takes, where they start and end, what happens on cancel, retry and refresh.
4. **Copy.** The exact words of labels, messages and errors, in the product's existing tone.
5. **Look.** For a new screen or a change to how one looks (a redesign, cleaner, modern, more visual), choose one
   visual direction with the user's UI design skill (`cpipe:run`, The user's own skills come first): the
   hierarchy and layout, the type scale, spacing, colors as the project's tokens or new ones with values, the one
   element that makes it distinctive, and motion with durations. Keep the design system for a small change; a
   request for a new look is not met by restating the current one. Then render it: a self-contained HTML mockup
   in `<run dir>/design/` with the real copy, built from the project's own fonts, tokens and component markup so
   the builder ports it rather than redraws it, and a desktop and a phone screenshot of it you have looked at.
   List the values the build must match, per width: sizes, spacing, colors, motion.
6. **Accessibility.** Labels for inputs, keyboard use and focus order, contrast, meaning not carried by color
   alone.
7. **Shape.** Narrow and wide screens for UI; fields, types, status codes and errors for an API; flags, output
   and exit codes for a CLI.
8. **Edge cases.** Long text, many items, no items, slow or failed requests, two people acting at once.

## Output

A design note: the surfaces, each state, the copy, the edge cases, and for a look change the direction's values
and the mockup's paths. A design profile writes it to
`<run dir>/design.md`; otherwise it goes into the plan. Each item becomes an acceptance criterion in the plan.
Product questions the code does not settle are decided in step 4 and recorded as assumptions.

## Design check

After QA, for a look change with a mockup and a `design` profile: that profile, resumed in its design session,
holds QA's after shots beside its mockup at the same widths and replies with what differs, each as the shot, what
is off, and the fix as values (`gap 24px, not 12px`), or `matches`. Its fixes are act findings for the fix round
(`steps/review.md`, section 3); the QA re-run of that round checks them against the mockup, and no second design
check follows. Without a design profile, QA's comparison with the mockup is the check.

## Rules

- Reuse the existing design system and patterns; a new pattern needs a written reason, which a request for a new
  look is.
- Never change the behavior of an existing surface the task does not name.
- Never drop accessibility for looks.
- No change to the repository in this step: the note and the mockup live in the run directory.

## Done when

The design note covers every state of every touched surface, or the step is marked not needed with its reason
in the run log.
