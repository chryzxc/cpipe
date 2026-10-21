# cpipe: the set-up step

Step 1 of `cpipe:run`, always the delivery run's own. It starts the long work (installs, the baseline) in the
background, so whoever explores (`steps/plan.md`) starts at once and the build never waits for the baseline.

## 1. Set up

1. **Repository and base.** `git rev-parse --show-toplevel`, `git remote -v`, `git status --short --branch`, the
   default branch. If the task names a repository, path or base branch, use it; if several repositories match,
   pick the one the conversation used. Fetch the base so it is current.
2. **Branch name.** Name the branch the way the project does: its instructions, your skills, or the pattern of
   its recent branches (`git branch -r --sort=-committerdate | head -20`), such as `<area>/fix/<slug>`. With no
   pattern, use `<type>/<short-slug>` with the type of the change (`fix`, `feat`, `refactor`, `chore`). Pull
   request tooling often reads the type from the branch name, so it must match the change: if explore finds a
   different kind, rename the branch with `git branch -m` before anything is pushed.
3. **Worktree.** A new branch and worktree from the base, beside the repository:
   `git worktree add -b <branch> <repo>-worktrees/<branch with / replaced by -> <base>`. Follow the project's own
   worktree convention instead if its instructions name one. If the name exists, add a suffix; never reuse or
   reset someone else's branch.
4. **Dependencies.** Find how the project installs them (its lockfile and CI show how): `npm ci`,
   `pnpm install --frozen-lockfile`, `yarn install --immutable`, `uv sync`, `pip install -e .` into the project's
   virtual environment. Always inside a worktree, never globally; do not change the lockfile unless the task is
   about dependencies. Copy local env files (`.env`) from the user's checkout only if the checks need them, and
   never print their contents. The install itself runs in step 8.
5. **Run log.** Start it and `now.md` (`cpipe:run`, Run log) with the task, the user's instructions word for word
   (from the task and from the conversation about this work), the base, the worktree and the roles line.
6. **Tidy up.** Remove this repository's finished cpipe worktrees so they do not fill the disk. A repository can
   have dozens of worktrees and branches, so never list them into the chat; this prints only the candidates:
   ```sh
   m=<run dir>/logs/merged.txt
   gh pr list -s merged -L 500 --json headRefOid,headRefName --jq '.[]|.headRefOid+" "+.headRefName' >"$m"
   git worktree list --porcelain | sed -n 's/^worktree //p' | while IFS= read -r p; do
     [ -f "$(git -C "$p" rev-parse --git-dir)/cpipe/run.md" ] || continue
     grep -qxF "$(git -C "$p" rev-parse HEAD) $(git -C "$p" branch --show-current)" "$m" && echo "$p"
   done
   ```
   A worktree is cpipe's when its git dir holds a run log, and a candidate when a merged pull request has its
   branch and its `HEAD` as the head commit. A branch with no merged pull request stays, even when it looks
   merged, because a run that has not committed yet looks the same. Never touch any other worktree, the user's
   checkout or this run's worktree. Remove a candidate only when also:
   - `git status --porcelain` is empty, apart from tracked files that match the ignore rules
     (`git ls-files -ci --exclude-standard`, such as runtime logs), which you restore with `git restore` first;
   - no process has its working directory inside it (`lsof -d cwd -Fn`).

   Then `git worktree remove <path>` (never `--force`) and `git worktree prune`; the branch stays. Name what was
   removed in the run log. Anything unclear stays; a failed removal is noted and never ends the run.
7. **Checks.** Find the project's own commands in its files, not its code: `package.json` scripts, `Makefile`,
   `pyproject.toml`, the CI workflow files (they show how CI installs and tests, and how long it gives each job:
   `timeout-minutes`), the development section of its instructions. Note the build or typecheck, the lint, the
   full suite, and how the test runner takes one file or a filter. Read only those files.
8. **Background work.** Write the time limit (below) to `<run dir>/limit.pl`, then start one background command
   with a completion notification that does the rest, one command after the other, each under the time limit
   with its output and exit code in its own file
   (`perl <run dir>/limit.pl <seconds> <command> > logs/<name>.txt 2>&1; echo "exit $?" >> logs/<name>.txt`):
   1. the install in the worktree (step 4), then `touch logs/install.done`;
   2. the **base worktree**: `git worktree add --detach "$(mktemp -d)/base" <base commit>`, the env files step 4
      copied, and the same install there; its own install, since dependencies linked from the delivery worktree
      can point back to its code (workspace links, an editable install);
   3. the **baseline**, in the base worktree: the checks from step 7 (the full suite, not only the narrow tests,
      then the build) the way `steps/verify.md` (Fast checks) runs them, with no serial or single-worker flags,
      to `logs/baseline-<check>.txt`;
   4. `touch logs/baseline.done`, last and whatever came before.

   Write its process id and the base worktree's path in the run log and `now.md`, and start whoever explores at
   once. Only verify waits for the baseline; explore and build wait for `logs/install.done`, and only before
   they run something in the worktree. If the base worktree cannot be added or installed, run the baseline in
   the delivery worktree instead: then, and only then, the build starts after `logs/baseline.done`, so that no
   edit spoils it.

When the background work ends, record each check's result in the run log: pass, fail (which tests), not finished
(`exit 124`: it hit its time limit, so a test hangs or the suite is slower than its limit), or not run here (why).
Save the ids of the failing tests and lint errors, one per line and sorted, to `baseline-<check>.txt` beside the run
log, so verify can compare by name, not by count. A check that did not finish gets a line in `now.md` too: verify
never runs it whole (`steps/verify.md`, After the baseline). A check already red is the baseline, not your job,
unless the task is to fix it. A check that will not run is worked through `steps/verify.md` (When a check will not
run), so the plan names commands that work. If an install fails, try the next way from there and record it; a failed
install never ends the run. Deliveries of one run from the same base commit share one baseline and its worktree:
copy the first one's `baseline-<check>.txt` files and `logs/baseline.done` instead of running it again.

## Time limit

A hung test (an open handle, a server that never closes) never ends its command, so its completion notification
never comes, and nothing else may wake the run. Every background command the run starts (an install, a check, a
profile) runs under this limit: at the limit it stops the command and everything it started, and exits 124.

```perl
# perl limit.pl <seconds> <command> [<arguments>]
my $t = shift; my $p = fork // die "fork: $!";
if (!$p) { setpgrp; exec @ARGV or exit 127 }
$SIG{ALRM} = $SIG{TERM} = $SIG{INT} = sub { kill 'TERM', -$p; sleep 5; kill 'KILL', -$p; exit 124 };
alarm $t; waitpid $p, 0; exit($? >> 8 or $? & 127);
```

A command with shell syntax of its own goes in as `sh -c '<command>'`. Limits: an install 15 minutes; a baseline
check its CI time limit, else 20 minutes; a check in verify twice its time on the base, at least 5 minutes; a
profile its run budget plus 5 minutes. Without perl, a script file that does the same. The limit is the one thing
allowed around a project's command (`steps/verify.md`, The project's commands only).

## Rules

- Nothing outside the worktree changes in this step except the tidy-up and the base worktree.
- Set up reads no code: the project's files that name its commands, and nothing else.

## Done when

The worktree exists, the run log and `now.md` are started, the checks are found, and the background work runs
with its process id and the base worktree's path in `now.md`.
