# Refactor playbook

**Proof required:** the same checks green before and after, with behavior unchanged.

1. **Baseline.** Run the plan's checks on the base before any change and save the result. That is the oracle.
   Where tests are thin, write a characterization test of the current behavior first.
2. **Plan the moves.** A sequence of small structural moves, each one verifiable on its own.
3. **One move at a time.** Make it, run the checks, fix anything it broke in the same move, record it.
4. **Compare.** After the last move, run the full checks and compare with the baseline. Any difference in
   behavior is a bug: fix it or revert the move.

A refactor that changes behavior is a feature or a bug. Say which in the report.

If the baseline itself is red, record which checks were already failing. Only new failures count against you.
