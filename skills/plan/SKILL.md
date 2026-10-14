---
name: plan
description: "cpipe: understand, clarify once, plan, get approval."
version: 3.0.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delivery, planning, clarify]
    related_skills: [cpipe:run, cpipe:build]
---

# cpipe: plan

Steps 1 to 3 of `cpipe:run`. Spend your reading here once, so the build does not repeat it.

## 1. Understand

1. **Repository.** `git rev-parse --show-toplevel`, `git remote -v`, `git status --short --branch`, the default
   branch. If the task names a repository or path, use it; if several match, pick the one the conversation used.
2. **Instructions.** Read the project's agent and contributor instructions (`AGENTS.md`, `CLAUDE.md`,
   `CONTRIBUTING.md`, the README's development section) and follow them over these skills where they conflict.
3. **Code.** If you have a code index or code-navigation tool, locate the code with it first. Read only the code
   the task touches and its direct callers. About fifteen reads is plenty before the plan; read more while building.
4. **Checks.** Find the project's own commands: `package.json` scripts, `Makefile`, `pyproject.toml`, the CI
   workflow files (they show exactly how CI installs and tests). Prefer the narrowest command that covers the change
   (one test file or directory), plus the build or typecheck if the project has one.
5. **Baseline.** Run those checks once on the base now and record the result: pass, fail (which tests), or cannot
   run here (why). A check that is already red is the baseline, not your job, unless the task is to fix it. If a
   check cannot run, try the fixes in `cpipe:verify` now, so the plan names commands that work.
6. **Kind.** bug, feature, refactor or chore (see `cpipe:run`).

## 2. Clarify once

Ask only what you cannot default and what changes the result: a product decision, which of two behaviors is
wanted, an external system you have no access to. Put every question in one message with your proposed default
for each, so the user can answer "defaults are fine". Everything else becomes an assumption in the plan.

If the request and the code settle everything, ask nothing.

## 3. Plan

If the roles file names a `plan` profile, have it draft the plan (`roles.md` in `cpipe:run`) and check the draft
against what you found. Write a short plan in the chat (save it to the run log too):

- **Goal:** one sentence, in the user's words.
- **Kind** and playbook.
- **Base:** branch and commit.
- **Scope:** the files or directories you expect to change. **Non-goals:** what you will not touch.
- **Acceptance:** observable criteria, each one checkable.
- **Checks:** the exact commands, with the baseline result of each.
- **Assumptions** and **risks.**
- **Deliverable:** a local commit on a new branch (default), or a draft pull request if the user asked for one.

Then ask once: "Approve and I will build it." Treat "yes", "proceed", "go" or similar as approval. Changes to the
plan get a revised plan and one more approval. If the user already told you to go ahead without a plan review,
show the plan and start.

Keep it short. The plan is for the user to approve in a minute, not a specification.
