# Review playbook

Review a change that already exists (a pull request, a branch, a commit) when no new work is asked.

**Proof required:** the change verified on its own head commit, QA on the real surface, an independent review,
and a verdict backed by them.

1. **Set up on the head.** Check out the exact head commit in a worktree (an existing one for this change, or a
   new one). Record the commit; the verdict is for that commit only.
2. **Explore and baseline** as usual: the flow map of what the change touches and the checks on its base, so a
   failure is attributed to the change only when the base passes.
3. **Verify, QA and review** the change as it is. Design, decide and plan cover only what the review needs.
4. **Build only on findings.** With no **act** findings, build and clean are — (`nothing to fix`). With some, fix
   them when the task asks for fixes or the change came from an earlier delivery of this run's worktree (its run
   log is there), then verify and the delta review (`steps/review.md`, section 3); otherwise the verdict is
   changes needed, with each finding.
5. **Fixes reach the pull request** when the task asks to fix it or make it right ("make sure there are no
   regressions", "fix the findings") or the user asked for that pull request earlier in this work: commit, push to
   its branch, update its body and wait for its CI (`steps/report.md`, Pull request). Otherwise they stay
   committed locally and the report gives the push command.
6. **Never** approve on the hosting service, merge, mark ready or open another pull request unless the task asks.
