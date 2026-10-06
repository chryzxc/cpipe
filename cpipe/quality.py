"""The checks node: the operator's quality checks, run on a delivery's changed files. cpipe ships no checks.

The checks live in ``$HERMES_HOME/delivery/quality.yaml``; cpipe only decides when they run::

    checks:
      - name: lint
        files: "*.js *.ts *.vue"              # fnmatch globs on the path; skipped when no changed file matches
        run: "~/.hermes/scripts/lint.sh {base} {files}"   # {files}: the matches, shell-quoted; {base}: diff base sha
        projects: [my-app]                    # optional: only deliveries of these projects
        stage: security                       # optional: run with QA and review (verify), not after each build

Checks of a stage run in parallel.

A failing check (non-zero exit) goes back to the build session with its output. A check that hangs past
CHECK_TIMEOUT is skipped, never blocking. A check whose tool cannot start or crashes (not on PATH, exit 126/127, a
traceback) is the machine's problem, not the change's: it is skipped and written to ``state/setup-problems.json``.
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from .home import root as _root_home

CHECK_TIMEOUT = 180
ORPHAN_POLL = 5  # seconds a check outlives the worker that started it
OUTPUT_KEEP = 2500  # chars of a failing check's output shown to the implementer
# the tool, not the checked code, failed: semgrep crashed on an import and refused every change
CRASH = re.compile(r"Traceback \(most recent call last\)|ModuleNotFoundError|ImportError:|command not found", re.I)


def load(home: Optional[Path] = None) -> dict:
    try:
        import yaml
        data = yaml.safe_load(((home or _root_home()) / "delivery" / "quality.yaml").read_text()) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _checks(cfg: dict, project: Optional[str]) -> list[dict]:
    return [c for c in cfg.get("checks") or [] if isinstance(c, dict) and c.get("run")
            and (not c.get("projects") or project in c["projects"])]


def diff_base(ws: Path, base_branch: Optional[str]) -> Optional[str]:
    """The whole branch, from where it left its base: the checks run once, on the final check before merge."""
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
    # Its own session lets the timeout kill the whole group, but a worker that dies first never runs the timeout:
    # the watcher kills the group once this process is gone (orphaned eslint runs piled up for an hour).
    cmd = (f"( while kill -0 {os.getpid()} 2>/dev/null; do sleep {ORPHAN_POLL}; done; kill -9 0 ) "
           f">/dev/null 2>&1 & w=$!\n{{ {cmd}\n}}\nrc=$?; kill $w 2>/dev/null; exit $rc")
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


def _names_any(output: str, files: list[str]) -> bool:
    """A traceback through a changed file is the change's failure (a broken import), not the tool's."""
    return any(f in output for f in files)


def run_checks(ws: Path, base: str, project: Optional[str], home: Optional[Path] = None,
               card: Optional[str] = None, stage: str = "checks") -> list[str]:
    """Failures as ``name: output`` blocks; [] when everything passed, nothing matched, or nothing is set up."""
    checks = [c for c in _checks(load(home), project) if (c.get("stage") or "checks") == stage]
    files = changed_files(ws, base) if checks else []

    def one(c: dict) -> Optional[str]:
        globs = str(c.get("files") or "*").split()
        hits = [f for f in files if any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(Path(f).name, g) for g in globs)]
        if not hits:
            return None
        cmd = str(c["run"]).replace("{base}", shlex.quote(base)).replace("{files}", " ".join(map(shlex.quote, hits)))
        done = _run(os.path.expanduser(cmd) if cmd.startswith("~") else cmd, ws)
        if done and (done.returncode in (126, 127) or (CRASH.search(done.stdout or "") and not _names_any(done.stdout, files))):
            report_setup(str(c.get("name", "check")), f"`{c['run']}` exit {done.returncode}: {done.stdout or ''}",
                         card, home)
            return None
        if done:
            report_setup(str(c.get("name", "check")), None, home=home)  # it ran: any old setup problem is fixed
        if done and done.returncode != 0:
            return f"{c.get('name', 'check')}: `{c['run']}`\n{(done.stdout or '').strip()[-OUTPUT_KEEP:]}"
        return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        return [f for f in pool.map(one, checks) if f]
