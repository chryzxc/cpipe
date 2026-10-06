"""The setup node: make a delivery's worktree usable before the engine's first turn. Deterministic, no LLM.

  * a fresh worktree (clean, nothing committed or pushed) is reset onto ``origin/<base_branch>``,
  * each missing ``node_modules`` is symlinked from the repo's main checkout,
  * the main checkout's code index (``.codegraph/``) is cloned copy-on-write and synced in the background.

Project profiles, ``$HERMES_HOME/delivery/projects.yaml``::

    projects:
      my-app:
        base_branch: develop        # PRs target this; worktrees must be based on origin/<it>
        branch_prefix: feat/        # optional
        conventions: "squash-merge; conventional commits"   # optional, ≤10 lines, put in the build prompt
        qa: browser                 # browser | computer-use | off
        test_env: {...}             # see test_env.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .home import root as _root_home

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
    # a symlink is a file: `node_modules/` in .gitignore misses it, so a `git add -A` would commit it
    _exclude(Path(common), [f"/{r}/node_modules".replace("/./", "/") for r in linked])
    return linked


def _exclude(common: Path, patterns: list[str]) -> None:
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    have = exclude.read_text().splitlines() if exclude.is_file() else []
    extra = [p for p in patterns if p not in have]
    if extra:
        exclude.write_text("\n".join(have + extra) + "\n")


def link_codegraph(ws: Path) -> bool:
    """Clone the main checkout's codegraph index into the worktree and sync it in the background.
    Workers used it on 38 of ~22,700 tool calls because a fresh worktree had none; the brief only says
    to use it when `.codegraph/` exists. Copy-on-write only: a full copy of a 500 MB index is not worth it."""
    common = _git(ws, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common or (ws / ".codegraph").exists() or not shutil.which("codegraph"):
        return False
    main = Path(common).parent
    db = main / ".codegraph" / "codegraph.db"
    if main.resolve() == ws.resolve() or not db.is_file():
        return False
    target = ws / ".codegraph"
    target.mkdir()
    clone = ["cp", "-c"] if sys.platform == "darwin" else ["cp", "--reflink=always"]
    if subprocess.run([*clone, str(db), str(target)], capture_output=True).returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        return False
    (target / "source.json").write_text(json.dumps({"sourceDir": str(ws), "version": 1}, indent=2) + "\n")
    _exclude(Path(common), ["/.codegraph"])
    _sync_codegraph(ws)
    return True


def _sync_codegraph(ws: Path) -> None:
    subprocess.Popen(["codegraph", "sync", str(ws)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def project_profile(project: Optional[str], home: Optional[Path] = None) -> dict:
    """The operator's saved conventions for a project, {} when none. Never raises."""
    return projects(home).get(project) or {} if project else {}


def projects(home: Optional[Path] = None) -> dict:
    try:
        import yaml
        data = yaml.safe_load(((home or _root_home()) / "delivery" / "projects.yaml").read_text()) or {}
        return {k: v for k, v in (data.get("projects") or {}).items() if isinstance(v, dict)}
    except Exception:
        return {}


def project_of(ws: Path, home: Optional[Path] = None) -> Optional[str]:
    """The projects.yaml entry whose ``repo`` is this worktree's main checkout."""
    common = _git(ws, "rev-parse", "--path-format=absolute", "--git-common-dir")
    root = Path(common).parent.resolve() if common else None
    return next((name for name, p in projects(home).items()
                 if root and p.get("repo") and Path(p["repo"]).expanduser().resolve() == root), None)


def lessons(project: Optional[str], home: Optional[Path] = None, keep: int = 10) -> list[str]:
    """The operator's lessons for this project and for "*": ``delivery/lessons.yaml`` ``<project>: [text | {text}]``."""
    try:
        import yaml
        data = yaml.safe_load(((home or _root_home()) / "delivery" / "lessons.yaml").read_text()) or {}
    except Exception:
        return []
    items = [*(data.get("*") or []), *((data.get(project) or []) if project else [])]
    texts = [str(i.get("text") if isinstance(i, dict) else i).strip() for i in items]
    return [t for t in texts if t and t != "None"][-keep:]


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


def prepare(ws: Path, base: Optional[str]) -> list[str]:
    """The setup node: a fresh worktree on origin/<base>, node_modules and the code index linked from the main
    checkout. Returns notes for the engine's first prompt."""
    notes = []
    common = _git(ws, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common:
        _exclude(Path(common), [".cpipe/"])
    reset = rebase_fresh_worktree(ws, base)
    notes += [reset] if reset else []
    linked = link_node_modules(ws)
    if linked:
        notes.append(f"node_modules is symlinked from the main checkout for: {', '.join(linked)}. If this change "
                     "touches dependencies, replace the symlink with a real install.")
    if link_codegraph(ws):
        notes.append("`.codegraph/` (the repo's code index) is here: find code with `codegraph explore` first.")
    missing = [str(r) for r in _package_dirs(ws) if not (ws / r / "node_modules").exists()]
    if missing:
        notes.append(f"No node_modules for: {', '.join(missing)}. Install from the lockfile before testing.")
    return notes
