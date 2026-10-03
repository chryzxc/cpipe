"""The operator's quality setup, applied by the flow. cpipe ships no checks, skills, or style rules.

Every Hermes setup differs (skills, linters, design system), so the checks and the per-role guidance live in
``$HERMES_HOME/delivery/quality.yaml`` (the root home); cpipe only decides when they run and what they block::

    checks:          # run in the worktree on the implementer's changed files when it requests review
      - name: lint
        files: "*.js *.ts *.vue"              # fnmatch globs on the path; skipped when no changed file matches
        run: "~/.hermes/scripts/lint.sh {base} {files}"   # {files}: the matches, shell-quoted; {base}: diff base sha
        projects: [my-app]                    # optional: only cards of these projects
    guidance:        # added to the first turn of every card worked by the profile mapped to that role
      implementer: "For new UI load skill X; reuse the app's component library."
      reviewer: "..."

A failing check (non-zero exit) blocks the review request with its output, so the reviewer never spends a round
on what a command can find. A check that hangs past CHECK_TIMEOUT is skipped, never blocking.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import signal
import subprocess
from pathlib import Path
from typing import Optional

from .home import root as _root_home

CHECK_TIMEOUT = 180
OUTPUT_KEEP = 2500  # chars of a failing check's output shown to the implementer
MAX_BLOCKS = 3  # blocked review requests per card per run; then it goes to the reviewer with the failures
FIX_BASE = re.compile(r"^FIX BASE: ([0-9a-f]{7,40}) ", re.M)
_blocked: dict = {}


def load(home: Optional[Path] = None) -> dict:
    try:
        import yaml
        data = yaml.safe_load(((home or _root_home()) / "delivery" / "quality.yaml").read_text()) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def roles_of(profile: str, home: Optional[Path] = None) -> list[str]:
    """Workflow roles mapped to a profile in roster.yaml."""
    try:
        import yaml
        roles = (yaml.safe_load(((home or _root_home()) / "roster.yaml").read_text()) or {}).get("roles") or {}
    except Exception:
        return []
    return [r for r, p in roles.items() if str(p).strip() == profile]


def _checks(cfg: dict, project: Optional[str]) -> list[dict]:
    return [c for c in cfg.get("checks") or [] if isinstance(c, dict) and c.get("run")
            and (not c.get("projects") or project in c["projects"])]


def first_turn_notes(profile: str, project: Optional[str], home: Optional[Path] = None) -> list[str]:
    cfg = load(home)
    roles = roles_of(profile, home)
    notes = [str(text).strip() for r, text in (cfg.get("guidance") or {}).items() if r in roles and text]
    checks = _checks(cfg, project)
    if "implementer" in roles and checks:
        notes.append("Before your review request is filed these run on your changed files and refuse it on "
                     "failure; run them yourself first: "
                     + "; ".join(f"{c.get('name', 'check')}: `{c['run']}`" for c in checks) + ".")
    return notes


def diff_base(ws: Path, body: str, base_branch: Optional[str]) -> Optional[str]:
    """A fix round is checked from its FIX BASE; a build from where it left the project's base branch."""
    if m := FIX_BASE.search(body or ""):
        return m[1]
    for ref in ([f"origin/{base_branch}"] if base_branch else []) + ["origin/HEAD"]:
        out = subprocess.run(["git", "-C", str(ws), "merge-base", ref, "HEAD"], capture_output=True, text=True)
        if out.returncode == 0:
            return out.stdout.strip()
    return None


def changed_files(ws: Path, base: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(ws), "diff", "--name-only", "--diff-filter=ACMR", base, "HEAD"],
                         capture_output=True, text=True)
    return [f for f in out.stdout.splitlines() if f]


def _run(cmd: str, ws: Path) -> Optional[subprocess.CompletedProcess]:
    proc = subprocess.Popen(cmd, shell=True, cwd=ws, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=CHECK_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return None
    return subprocess.CompletedProcess(cmd, proc.returncode, out)


def run_checks(ws: Path, base: str, project: Optional[str], home: Optional[Path] = None) -> list[str]:
    """Failures as ``name: output`` blocks; [] when everything passed, nothing matched, or nothing is set up."""
    checks = _checks(load(home), project)
    files = changed_files(ws, base) if checks else []
    failures = []
    for c in checks:
        globs = str(c.get("files") or "*").split()
        hits = [f for f in files if any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(Path(f).name, g) for g in globs)]
        if not hits:
            continue
        cmd = str(c["run"]).replace("{base}", shlex.quote(base)).replace("{files}", " ".join(map(shlex.quote, hits)))
        done = _run(os.path.expanduser(cmd) if cmd.startswith("~") else cmd, ws)
        if done and done.returncode != 0:
            failures.append(f"{c.get('name', 'check')}: `{c['run']}`\n{(done.stdout or '').strip()[-OUTPUT_KEEP:]}")
    return failures


def review_request_gate(tid: str, row, base_branch: Optional[str], home: Optional[Path] = None):
    ws = Path(row["workspace_path"] or ".")
    base = diff_base(ws, row["body"] or "", base_branch)
    if not base or _blocked.get(tid, 0) >= MAX_BLOCKS:
        return None
    failures = run_checks(ws, base, row["project_id"], home)
    if not failures:
        return None
    _blocked[tid] = _blocked.get(tid, 0) + 1
    return {"action": "block", "message": (
        "Review not filed: the setup's quality checks failed on your changed files. Fix them, commit, push, and "
        "request review again (a failure your change did not cause: say so in the summary; after "
        f"{MAX_BLOCKS} refusals the request goes through with it).\n\n" + "\n\n".join(failures))}
