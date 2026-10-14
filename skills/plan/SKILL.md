---
name: plan
description: "cpipe: set up, explore, clarify once, plan, approval."
version: 3.1.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delivery, planning, clarify]
    related_skills: [cpipe:run, cpipe:design, cpipe:build]
---

# cpipe: plan

Steps 1, 2, 4 and 5 of `cpipe:run` (step 3 is `cpipe:design`). Spend the reading here, once, so the build does
not repeat it.

## 1. Set up

1. **Repository and base.** `git rev-parse --show-toplevel`, `git remote -v`, `git status --short --branch`, the
   default branch. If the task names a repository, path or base branch, use it; if several repositories match,
   pick the one the conversation used. Fetch the base so it is current.
2. **Worktree.** A new branch and worktree from the base, beside the repository:
   `git worktree add -b cp/<short-slug> <repo>-worktrees/cp-<short-slug> <base>`. Follow the project's own
   worktree convention instead if its instructions name one. If the name exists, add a suffix; never reuse or
   reset someone else's branch.
3. **Dependencies.** Install them inside the worktree the way the project does (its lockfile and CI show how):
   `npm ci`, `pnpm install --frozen-lockfile`, `yarn install --immutable`, `uv sync`, `pip install -e .` into
   the project's virtual environment. Never install globally; do not change the lockfile unless the task is about
   dependencies. Copy local env files (`.env`) from the user's checkout only if the checks need them, and never
   print their contents.
4. **Run log.** Start it (`cpipe:run`) with the task, the base, the worktree and the roles line.

If the install fails, try the next way from `cpipe:verify` and record it; a failed install never ends the run.

## 2. Explore

If the roles file names an `explore` profile, have it map the code (`roles.md` in `cpipe:run`) and open the
files it cites to confirm. Otherwise do it yourself:

1. **Instructions.** Read the project's agent and contributor instructions (`AGENTS.md`, `CLAUDE.md`,
   `CONTRIBUTING.md`, the README's development section). They win over these skills where they conflict, except
   for the guard rails in `cpipe:run`.
2. **Code.** If you have a code index or code-navigation tool, locate the code with it first. Read only the code
   the task touches, its direct callers and the helpers you could reuse. About fifteen reads is plenty here.
3. **Checks.** Find the project's own commands: `package.json` scripts, `Makefile`, `pyproject.toml`, the CI
   workflow files (they show how CI installs and tests). Pick the narrowest command that covers the change, plus
   the build or typecheck if the project has one, plus the broader suite for QA.
4. **Baseline.** Run those checks once in the worktree, before any change, and record each result: pass, fail
   (which tests) or not run here (why). A check already red is the baseline, not your job, unless the task is to
   fix it. If a check will not run, work through `cpipe:verify` now, so the plan names commands that work.
5. **Kind.** bug, feature, refactor or chore (`cpipe:run`). Load the playbook.

## 4. Clarify once

Ask only what you cannot default and what changes the result: a product decision, which of two behaviors is
wanted, an external system you have no access to. Put every question in one message, each with your proposed
default, so the user can answer "defaults are fine". Everything else becomes an assumption in the plan. If the
request and the code settle everything, ask nothing.

## 5. Plan

If the roles file names a `plan` profile, have it draft the plan (`roles.md` in `cpipe:run`), then check the
draft against the code and the baseline yourself. Write the plan in the chat and in the run log:

- **Goal:** one sentence, in the user's words.
- **Kind** and playbook.
- **Base** (branch and commit) and **worktree**.
- **Scope:** the files or directories you expect to change. **Non-goals:** what you will not touch.
- **Acceptance:** observable criteria, each one checkable, including the design note's states.
- **Checks:** the exact commands, with the baseline result of each.
- **Assumptions** and **risks.**
- **Deliverable:** a local commit on the delivery branch (default), or a draft pull request if asked.

Then ask once: "Approve and I will build it." "Yes", "proceed", "go" and similar are approval. Changes to the plan
get a revised plan and one more approval. If the user already said to go ahead without a plan review, show the
plan and start. If the user cancels, remove the worktree and branch you created (nothing is committed on them).

## Rules

- Nothing outside the worktree changes in these steps; exploring is read-only.
- The plan is for approval in a minute, not a specification: keep it short.
- Every acceptance criterion must be checkable by a test, a command or a QA step.
- Never ask the user something the code or a sensible default answers.

## Done when

The worktree exists with dependencies installed, the baseline is recorded, the plan is in the run log and it is
approved.
