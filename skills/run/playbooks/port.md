# Port playbook

Bring a change that exists elsewhere (another branch, commit, pull request or repository) into this base.

**Proof required:** every line the source change adds is ported or has its reason, and the proof of the change
it carries (its bug or feature playbook) holds on this base. On a screen it changes, this tree shows what the
source shows.

1. **Inventory first.** List every file and hunk of the source change (`git show --stat <commit>`,
   `git diff <from>..<to>`), including tests, migrations, config and docs. Write it in the run log as a table:
   `| Source file and hunk | Target file | Ported or not applicable (why) |`.
2. **Map each hunk** to where it belongs on this base. Code that moved or was renamed is found by its function
   or behavior, not its path. A hunk with no counterpart is not applicable only with the reason the base does
   not need it.
3. **Port** in the inventory's order: cherry-pick when it applies cleanly, otherwise apply each hunk by hand.
   Port the source's tests with the code.
4. **Line check.** A hunk row says what the hunk does, so a line inside it (a tooltip, a label, a guard) can be
   lost under a row marked ported. After each build round, run `port <source> <base>` (`cpipe:run`, Facts) to
   `logs/port.txt`. Each line it prints is ported now, or gets its own row: ported in another form (where) or not
   applicable (why). The code's files come first; a test file rewritten for this base is one row naming the test
   that carries it.
5. **Verify** with `steps/verify.md`. The completion gate also checks the inventory: every row is ported or
   has its reason, and every line of the last `logs/port.txt` has its row.
6. **Review and QA against the source.** The review brief carries `logs/port.txt`: the review checks each row
   against the source line, and a reason it cannot confirm is an **act** finding. QA puts each screen the source
   changes beside the same screen on this tree, the source's own screenshots when its pull request has them,
   and every control, label, tooltip and message the source shows is there.

The report's Summary names the source and links each not-applicable row to its reason.
