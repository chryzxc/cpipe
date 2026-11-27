# cpipe: role steps on the board

Part of `roles.md` in `cpipe:run`, loaded in board mode.

## On the board (`--kanban`)

When the task has a leading `--kanban` flag or the parallel lane brief records board mode, hand each named role
step over as a card on the user's Hermes kanban board instead of running the profile directly. Everything else stays the same: the brief,
the budgets, the checks you run yourself and the fallback. A role without a profile runs directly, as above: a card
cannot take this chat's model.

1. **Brief.** The card's body is the brief behind the card header (Card in `header.md`):
   `python3 <skill dir>/bin/cpipe.py brief <run dir> <worktree> <role> <round> --card > <run dir>/card-<role>.md`

2. **Create the card:**
   `hermes kanban create "cpipe <role>: <goal>" --assignee <profile> --body-file <run dir>/card-<role>.md --workspace dir:<worktree> --max-runtime <budget>s --max-retries 1 --idempotency-key <branch>-<role>-<round> --json`
   Use the lane's recorded board for every create, read and transition. Write the card id and round in the run
   log before waiting, and reuse that card on resume rather than creating a replacement. A follow-up to the same
   role is a new card with `--parent <previous card id>` when that card completed successfully.
3. **Wait** with one background command and a completion notification: it checks
   `hermes kanban show <card id> --json` every 30 seconds and exits when the status is `done`, `blocked`, `review`
   or `archived`, when it is still not claimed after 10 minutes, or 10 minutes past the card's budget whatever its
   status, so the wait always ends (a script file if the approval policy blocks the loop). Keep working on
   anything that does not depend on it, then end the turn (`cpipe:run`, Waiting ends the turn).
   **In a lane**, wait on that same bounded watcher in the current turn in chunks shorter than the tool timeout;
   a wait timeout is not a card failure. Continue from the recorded card ID rather than ending on a notification
   or starting the role again.
4. **Read** the result from `latest_summary` and the comments. It is a claim, as below.

The card failed if it ends blocked or with no usable result, is still running two minutes past its budget, or is
still not claimed after 10 minutes
(no dispatcher is running). Archive it (`hermes kanban archive <card id>`), note why, and follow "When a role
fails". Confirm the card no longer has a live worker before starting a fallback that uses its worktree.
Only ever touch cards this run created. Completing a role card does not complete a pre-existing source card.
