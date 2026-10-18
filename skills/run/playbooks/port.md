# Port playbook

Bring a change that exists elsewhere (another branch, commit, pull request or repository) into this base.

**Proof required:** every hunk of the source change is ported or marked not applicable with its reason, and the
proof of the change it carries (its bug or feature playbook) holds on this base.

1. **Inventory first.** List every file and hunk of the source change (`git show --stat <commit>`,
   `git diff <from>..<to>`), including tests, migrations, config and docs. Write it in the run log as a table:
   `| Source file and hunk | Target file | Ported or not applicable (why) |`.
2. **Map each hunk** to where it belongs on this base. Code that moved or was renamed is found by its function
   or behavior, not its path. A hunk with no counterpart is not applicable only with the reason the base does
   not need it.
3. **Port** in the inventory's order: cherry-pick when it applies cleanly, otherwise apply each hunk by hand.
   Port the source's tests with the code.
4. **Verify** with `steps/verify.md`. The completion gate also checks the inventory: every row is ported or
   has its reason.

The report's Summary names the source and links each not-applicable row to its reason.
