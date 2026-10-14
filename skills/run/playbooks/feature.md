# Feature playbook

**Proof required:** new tests that failed before the change and pass after it, the existing checks still green,
and the behavior exercised on the real surface where it is reachable.

1. **Understand what exists.** Read the modules the feature touches and reuse their helpers, types and patterns.
   Name the data shape before writing logic.
2. **Pin what must not change.** If a change could hide a regression, make sure a test covers the current
   behavior first.
3. **RED.** Write tests for the new behavior from the plan's acceptance criteria. Run them; they must fail on an
   assertion. Record the output.
4. **GREEN.** Implement in small verified steps: one change, run the narrow test, next change. Never two
   unverified changes in a row.
5. **Surface.** Exercise the feature where a user meets it when you can: the CLI, an API call, the page. Tests
   alone do not prove a feature. If the surface is not reachable here (no browser, no service), record it.
6. **Verify** with `cpipe:verify`.

Large features: split into units that never touch the same file. Do them in order yourself, or hand independent
units to subagents in parallel with exact file lists, and review every diff they return yourself.
