# Hotfix playbook

Production is broken now; time matters more than breadth.

**Proof required:** the failure reproduced and gone after the fix, the narrow checks green, one review round.

1. **Smallest fix that stops the failure.** No refactor, no cleanup beyond the changed lines, no extra
   features. Load the bug playbook and reproduce first.
2. **Narrow checks.** The tests for the changed code, lint and typecheck of the changed files, the build. The
   full suite runs once, at the end.
3. **One review round** on the fix. Act on findings that would break production; the rest go in Not in this
   change.
4. **Root cause later.** When the real fix is larger than the hotfix (a data repair, a redesign, a missing
   guard elsewhere), name it in the report as a separate delivery, with what it should cover.
