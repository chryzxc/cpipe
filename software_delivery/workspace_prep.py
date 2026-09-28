"""Make a kanban worker's workspace usable before the model's first turn. Deterministic, no LLM.

Two causes blocked most cards: a fresh git worktree has no ``node_modules`` (tests and lint
cannot run, and briefs forbid installs), and follow-up cards created without a workspace land
in an empty scratch dir. On the first turn of a kanban worker session this hook
  * symlinks each missing ``node_modules`` from the repo's main checkout, and
  * when the workspace is not a git repo, points the worker at its parent card's worktree.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Optional

PACKAGE_DEPTH = 2  # package.json at the root and one or two levels down (client/, server/, apps/x/)
SKIP_DIRS = {"node_modules", ".git", ".worktrees", "dist", "build"}


def _git(ws: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, timeout=10)
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


def prepare_workspace(*, is_first_turn: bool = False, **_: Any) -> Optional[dict]:
    """``pre_llm_call`` hook for kanban workers; silent everywhere else."""
    ws_env, task_id = os.environ.get("HERMES_KANBAN_WORKSPACE"), os.environ.get("HERMES_KANBAN_TASK")
    if not (is_first_turn and ws_env and task_id):
        return None
    try:
        ws = Path(ws_env)
        notes = []
        if _git(ws, "rev-parse", "--is-inside-work-tree") == "true":
            linked = link_node_modules(ws)
            if linked:
                notes.append("node_modules was missing in this worktree and is now symlinked from the main "
                             f"checkout for: {', '.join(linked)}. Run tests/lint normally; do not reinstall. "
                             "If a dependency this branch changed is missing, record it as READY_WITH_RISK.")
        else:
            db = Path(os.environ.get("HERMES_KANBAN_DB") or Path.home() / ".hermes" / "kanban.db")
            parent = parent_worktree(task_id, db)
            if parent:
                notes.append(f"Your assigned workspace {ws} is empty scratch, not the repo. The parent card "
                             f"worked in {parent}; cd there and work on its current branch. Do not block "
                             "for a missing repository.")
        return {"context": "[software-delivery workspace] " + " ".join(notes)} if notes else None
    except Exception:
        return None
