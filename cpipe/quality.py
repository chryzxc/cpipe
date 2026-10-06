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
on what a command can find. A check that hangs past CHECK_TIMEOUT is skipped, never blocking. A check whose tool
cannot start or crashes (not on PATH, exit 126/127, a traceback) is the machine's problem, not the change's: it is
skipped and written to ``state/setup-problems.json``, which board triage sends the operator.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from .home import root as _root_home

CHECK_TIMEOUT = 180
OUTPUT_KEEP = 2500  # chars of a failing check's output shown to the implementer
MAX_BLOCKS = 3  # blocked review requests per card per run; then it goes to the reviewer with the failures
FIX_BASE = re.compile(r"^FIX BASE: ([0-9a-f]{7,40}) ", re.M)
_blocked: dict = {}
# the tool, not the checked code, failed: semgrep crashed on an import and refused every review request
CRASH = re.compile(r"Traceback \(most recent call last\)|ModuleNotFoundError|ImportError:|command not found", re.I)


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
    missing = set(missing_tools(project, home)) if "implementer" in roles else set()
    for c in checks if "implementer" in roles else []:  # missing at an earlier start, installed since
        name = str(c.get("name", "check"))
        report_setup(name, MISSING if name in missing else None, os.environ.get("HERMES_KANBAN_TASK"), home,
                     only=MISSING)
    checks = [c for c in checks if str(c.get("name", "check")) not in missing]
    if missing:
        notes.append(f"These quality checks cannot run on this machine and are skipped (the operator is told): "
                     f"{', '.join(sorted(missing))}. Do not block for them.")
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
    # Hermes's own Python path would shadow the packages of Python-based checks (semgrep crashed on its mcp)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = subprocess.Popen(cmd, shell=True, cwd=ws, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=CHECK_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return None
    return subprocess.CompletedProcess(cmd, proc.returncode, out)


MISSING = "command not found on this machine"
SHELL_WORDS = {"!", "[", "test", "true", "false", "cd", "exec", "command", "time"}


def _tool(cmd: str) -> str:
    """The program a check runs: its first word that is not an env assignment."""
    try:
        words = shlex.split(cmd)
    except ValueError:
        words = cmd.split()
    return os.path.expanduser(next((w for w in words if "=" not in w), ""))


def missing_tools(project: Optional[str], home: Optional[Path] = None) -> list[str]:
    """Configured checks whose program is not on this machine: found before a worker starts, not at handoff."""
    import shutil
    return [str(c.get("name", "check")) for c in _checks(load(home), project)
            if (tool := _tool(str(c["run"]))) not in SHELL_WORDS and not shutil.which(tool)]


def report_setup(name: str, detail: Optional[str], card: Optional[str] = None, home: Optional[Path] = None,
                 only: Optional[str] = None) -> None:
    """Record a broken check tool for the operator (board triage digest); detail None clears it, or with `only`,
    clears it only when that was the recorded problem. Never raises."""
    try:
        path = (home or _root_home()) / "state" / "setup-problems.json"
        try:
            problems = json.loads(path.read_text())
        except (OSError, ValueError):
            problems = {}
        if detail is None and name in problems and only in (None, problems[name].get("detail")):
            del problems[name]
        elif detail is not None and name not in problems:
            problems[name] = {"detail": detail[-400:], "card": card, "at": time.time()}
        else:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(problems))
    except Exception:
        pass


def run_checks(ws: Path, base: str, project: Optional[str], home: Optional[Path] = None,
               card: Optional[str] = None) -> list[str]:
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
        if done and (done.returncode in (126, 127) or CRASH.search(done.stdout or "")):
            report_setup(str(c.get("name", "check")), f"`{c['run']}` exit {done.returncode}: {done.stdout or ''}",
                         card, home)
            continue
        if done:
            report_setup(str(c.get("name", "check")), None, home=home)  # it ran: any old setup problem is fixed
        if done and done.returncode != 0:
            failures.append(f"{c.get('name', 'check')}: `{c['run']}`\n{(done.stdout or '').strip()[-OUTPUT_KEEP:]}")
    return failures


def review_request_gate(tid: str, row, base_branch: Optional[str], home: Optional[Path] = None):
    ws = Path(row["workspace_path"] or ".")
    base = diff_base(ws, row["body"] or "", base_branch)
    if not base or _blocked.get(tid, 0) >= MAX_BLOCKS:
        return None
    failures = run_checks(ws, base, row["project_id"], home, tid)
    if not failures:
        return None
    _blocked[tid] = _blocked.get(tid, 0) + 1
    return {"action": "block", "message": (
        "Review not filed: the setup's quality checks failed on your changed files. Fix them, commit, push, and "
        "request review again (a failure your change did not cause: say so in the summary; after "
        f"{MAX_BLOCKS} refusals the request goes through with it).\n\n" + "\n\n".join(failures))}
