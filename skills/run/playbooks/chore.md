# Chore playbook

Docs, config, renames, dependency bumps and other changes without new behavior.

**Proof required:** the plan's checks green (or unchanged from the baseline) and the change read back.

1. Make the change.
2. Read it back as a stranger would: links resolve, names are consistent, nothing unrelated changed.
3. Run the checks with `steps/verify.md`. For a dependency bump, also run the build and the tests that use the
   dependency.

Docs describe the product as it is now, in plain sentences.
