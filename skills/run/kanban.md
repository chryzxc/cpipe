# cpipe: role steps on the board

Part of `roles.md` in `cpipe:run`, loaded when the task starts with `--kanban`.

## On the board (`--kanban`)

When the task message starts with `--kanban`, hand each role step over as a card on the user's Hermes kanban board
instead of running the profile directly, so the work shows on the board. Everything else stays the same: the brief,
the budgets, the checks you run yourself and the fallback. A role without a profile runs directly, as above: a card
cannot take this chat's model.

1. **Brief.** The card's body is the brief behind the card header (Card in `header.md`):
   `python3 <skill dir>/bin/cpipe.py brief <run dir> <worktree> <role> <round> --card > <run dir>/card-<role>.md`

2. **Create the card:**
   `hermes kanban create "cpipe <role>: <goal>" --assignee <profile> --body-file <run dir>/card-<role>.md --workspace dir:<worktree> --max-runtime <budget>s --max-retries 1 --idempotency-key <branch>-<role>-<round> --json`
   Write the card id in the run log. A follow-up to the same role is a new card with `--parent <previous card id>`.
3. **Wait** with one background command and a completion notification: it checks
   `hermes kanban show <card id> --json` every 30 seconds and exits when the status is `done`, `blocked`, `review`
   or `archived`, when it is still not claimed after 10 minutes, or 10 minutes past the card's budget whatever its
   status, so the wait always ends (a script file if the approval policy blocks the loop). Keep working on
   anything that does not depend on it, then end the turn (`cpipe:run`, Waiting ends the turn).
4. **Read** the result from `latest_summary` and the comments. It is a claim, as below.

The card failed if it ends blocked or with no usable result, is still running two minutes past its budget, or is
still not claimed after 10 minutes
(no dispatcher is running). Archive it (`hermes kanban archive <card id>`), note why, and follow "When a role
fails". Only ever touch cards this run created.
