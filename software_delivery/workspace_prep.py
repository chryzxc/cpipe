"""Make a kanban worker's workspace usable before the model's first turn. Deterministic, no LLM.

Two causes blocked most cards: a fresh git worktree has no ``node_modules`` (tests and lint
cannot run, and briefs forbid installs), and follow-up cards created without a workspace land
in an empty scratch dir. On the first turn of a kanban worker session this hook
  * symlinks each missing ``node_modules`` from the repo's main checkout,
  * when the workspace is not a git repo, points the worker at its parent card's worktree, and
  * applies the card's project profile (``$HERMES_HOME/delivery/projects.yaml``): a fresh worktree
    (clean, nothing committed or pushed) is reset onto ``origin/<base_branch>``, so a PR never starts
    on the wrong base; otherwise CONVENTION_MISMATCH notes, plus env setup and conventions.

projects.yaml::

    projects:
      my-app:
        base_branch: develop        # PRs target this; worktrees must be based on origin/<it>
        branch_prefix: feat/        # optional
        env: "cp .env.example .env; tests need a local Postgres"   # optional, told to workers
        conventions: "squash-merge; conventional commits"            # optional, told to workers
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Optional

PACKAGE_DEPTH = 2  # package.json at the root and one or two levels down (client/, server/, apps/x/)
SKIP_DIRS = {"node_modules", ".git", ".worktrees", "dist", "build"}


def _git(ws: Path, *args: str, timeout: int = 10) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _package_dirs(root: Path) -> list[Path]:
    """Relative dirs holding a package.json, at most PACKAGE_DEPTH below root."""
    found, frontier = [], [Path(".")]
    for depth in range(PACKAGE_DEPTH + 1):
        nxt = []
        for rel in frontier:
            if (root / rel / "package.json").is_file():
                found.append(rel)
            if depth < PACKAGE_DEPTH:
                try:
                    nxt += [rel / d.name for d in (root / rel).iterdir()
                            if d.is_dir() and d.name not in SKIP_DIRS and not d.name.startswith(".")]
                except OSError:
                    pass
        frontier = nxt
    return found


def link_node_modules(ws: Path) -> list[str]:
    """Symlink node_modules from the main checkout into the worktree. Returns linked dirs."""
    common = _git(ws, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return []
    main = Path(common).parent
    if main.resolve() == ws.resolve():
        return []  # this IS the main checkout
    linked = []
    for rel in _package_dirs(ws):
        target, source = ws / rel / "node_modules", main / rel / "node_modules"
        if not target.exists() and not target.is_symlink() and source.is_dir():
            target.symlink_to(source)
            linked.append(str(rel))
    if linked:  # a symlink is a file: `node_modules/` in .gitignore misses it, so a `git add -A` would commit it
        exclude = Path(common) / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        have = exclude.read_text().splitlines() if exclude.is_file() else []
        extra = [p for p in (f"/{r}/node_modules".replace("/./", "/") for r in linked) if p not in have]
        if extra:
            exclude.write_text("\n".join(have + extra) + "\n")
    return linked


def parent_worktree(task_id: str, db: Path) -> Optional[str]:
    """Most recent parent card workspace that is a git checkout."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT t.workspace_path FROM task_links l JOIN tasks t ON t.id = l.parent_id "
            "WHERE l.child_id = ? AND t.workspace_path IS NOT NULL "
            "ORDER BY coalesce(t.completed_at, t.created_at) DESC", (task_id,)).fetchall()
        conn.close()
    except sqlite3.Error:
        return None
    for (path,) in rows:
        if path and _git(Path(path), "rev-parse", "--is-inside-work-tree") == "true":
            return path
    return None


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def project_profile(project: Optional[str], home: Optional[Path] = None) -> dict:
    """The operator's saved conventions for a project, {} when none. Never raises."""
    if not project:
        return {}
    try:
        import yaml
        data = yaml.safe_load(((home or _home()) / "delivery" / "projects.yaml").read_text()) or {}
        profile = (data.get("projects") or {}).get(project) or {}
        return profile if isinstance(profile, dict) else {}
    except Exception:
        return {}


def profile_brief(profile: dict) -> str:
    """One paragraph for a card body or a worker's first turn."""
    parts = [f"PRs target `{profile['base_branch']}`" if profile.get("base_branch") else "",
             f"branch names start with `{profile['branch_prefix']}`" if profile.get("branch_prefix") else "",
             f"environment: {profile['env']}" if profile.get("env") else "",
             f"conventions: {profile['conventions']}" if profile.get("conventions") else ""]
    parts = [p for p in parts if p]
    return ("PROJECT CONVENTIONS (saved by the operator): " + "; ".join(parts) + ".") if parts else ""


def rebase_fresh_worktree(ws: Path, base: Optional[str]) -> Optional[str]:
    """Reset a worktree nobody has worked in yet onto origin/<base>. Anything committed, pushed, or
    edited is left alone: that is someone's work (a fix card reopens a pushed branch)."""
    if not base:
        return None
    _git(ws, "fetch", "-q", "origin", base, timeout=60)  # offline: fall back to the last fetched ref
    target = _git(ws, "rev-parse", "--verify", "-q", f"origin/{base}")
    branch = _git(ws, "rev-parse", "--abbrev-ref", "HEAD")
    if (not target or not branch or target == _git(ws, "rev-parse", "HEAD")
            or _git(ws, "status", "--porcelain") != ""
            or _git(ws, "rev-parse", "--verify", "-q", f"origin/{branch}") is not None
            or _git(ws, "rev-list", "HEAD", "--not", "--remotes") != ""):
        return None
    if _git(ws, "reset", "-q", "--hard", f"origin/{base}") is None:
        return None
    return f"This worktree was reset onto origin/{base} (the project's PR base) before you started."


def convention_notes(ws: Path, profile: dict) -> list[str]:
    notes = []
    base = profile.get("base_branch")
    if base and _git(ws, "rev-parse", "--verify", "-q", f"origin/{base}") is not None:
        if _git(ws, "merge-base", "--is-ancestor", f"origin/{base}", "HEAD") is None:
            notes.append(f"CONVENTION_MISMATCH: this worktree is not based on origin/{base}, the project's PR "
                         f"base. Before coding: `git fetch origin {base} && git rebase origin/{base}` (or "
                         "recreate the branch from it). Open the PR against it.")
    prefix = profile.get("branch_prefix")
    branch = _git(ws, "rev-parse", "--abbrev-ref", "HEAD")
    if prefix and branch and branch != "HEAD" and not branch.startswith(prefix):
        notes.append(f"CONVENTION_MISMATCH: branch `{branch}` should start with `{prefix}`; "
                     f"`git branch -m {prefix}{branch.rsplit('/', 1)[-1]}` before pushing.")
    brief = profile_brief(profile)
    return notes + ([brief] if brief else [])


def _card_project(task_id: str, db: Path) -> Optional[str]:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = conn.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def prepare_workspace(*, is_first_turn: bool = False, **_: Any) -> Optional[dict]:
    """``pre_llm_call`` hook for kanban workers; silent everywhere else."""
    ws_env, task_id = os.environ.get("HERMES_KANBAN_WORKSPACE"), os.environ.get("HERMES_KANBAN_TASK")
    if not (is_first_turn and ws_env and task_id):
        return None
    try:
        ws = Path(ws_env)
        notes = []
        db = Path(os.environ.get("HERMES_KANBAN_DB") or _home() / "kanban.db")
        profile = project_profile(_card_project(task_id, db))
        if _git(ws, "rev-parse", "--is-inside-work-tree") == "true":
            reset = rebase_fresh_worktree(ws, profile.get("base_branch"))
            notes += [reset] if reset else []
            linked = link_node_modules(ws)
            if linked:
                notes.append("node_modules was missing in this worktree and is now symlinked from the main "
                             f"checkout for: {', '.join(linked)}. Run tests/lint normally. If this branch "
                             "changes dependencies, replace the symlink with a real install.")
            missing = [str(r) for r in _package_dirs(ws) if not (ws / r / "node_modules").exists()]
            if missing:
                notes.append(f"No node_modules for: {', '.join(missing)}. Install them with the project's "
                             "package manager (lockfile install) before testing; do not block for it.")
            notes += convention_notes(ws, profile)
        else:
            parent = parent_worktree(task_id, db)
            if parent:
                notes.append(f"Your assigned workspace {ws} is empty scratch, not the repo. The parent card "
                             f"worked in {parent}; cd there and work on its current branch. Do not block "
                             "for a missing repository.")
        return {"context": "[software-delivery workspace] " + " ".join(notes)} if notes else None
    except Exception:
        return None
