# cpipe: UI design guide

cpipe's own guide for how a screen looks, used only when no UI design skill of the user's covers it (`SKILL.md`,
The user's own skills come first). Read it once, at the design step or the build of a look change.

## Pick a direction first

- **Who and what for.** Name the user and the job of the screen: operating, monitoring, configuring, comparing,
  reading, deciding, signing up. A tool screen is dense and calm; a landing page has room for one bold idea. Never
  give a working screen a marketing hero.
- **One idea.** Write the direction in one sentence (`quiet, editorial, one strong accent`, `dense and technical,
  monospace numbers`) and one element that makes this screen its own: a type pairing, an illustration style, a
  layout break, a data visual. Everything else stays plain so that one element reads.
- **Inside the product.** An existing app keeps its fonts, colors and components unless the task asks for a new
  look; then the new look is still one family with the rest of it.

## Make the choices concrete

- **Type.** At most two families. A scale with clear steps (for example 12, 14, 16, 20, 28, 40), body at 16 px
  or more, line height 1.4 to 1.6 for text and about 1.1 for large headings, line length 60 to 75 characters.
  Hierarchy comes from size and weight, not from many colors.
- **Spacing.** One scale (4 or 8 px steps) for every gap. Related things sit closer than unrelated ones; sections
  get clearly more room than items inside them. Edges line up on a grid.
- **Color.** Neutrals carry most of the screen; one accent for the primary action and the current state; status
  colors only for status. Text contrast at least 4.5:1 (3:1 for large text and controls). Dark mode, when there is
  one, is its own palette, not inverted colors.
- **Surfaces.** One way to separate areas (space, a hairline border or a soft shadow), used the same everywhere.
  Corner radius from one small set.
- **Motion.** Only where it explains something: a change of state, where something came from. 150 to 250 ms for
  feedback, up to 400 ms for entrances, ease-out; nothing that loops or blocks reading, and none with
  `prefers-reduced-motion`.

## Avoid the templated look

Defaults read as unfinished: a generic font left as the browser or framework set it, a purple-to-blue gradient
for no reason, every section centered at the same width, cards inside cards, icons on every heading, gray text on a
colored background, filler copy. Real copy and realistic data go in from the first draft.

## Check it by eye

Render it at a desktop and a phone width, look at the screenshots, and fix what is off before calling it done:
uneven gaps, edges that do not line up, a hierarchy that does not show what to do first, text that wraps badly,
controls that still look like browser defaults. Also the empty, loading and error states, and long text.
