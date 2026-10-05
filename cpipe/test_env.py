"""``delivery_test_env`` — run a card's or PR's branch locally for the user to test, the same way every time.

The recipe is the operator's, per project, in ``$HERMES_HOME/delivery/projects.yaml``::

    test_env:
      setup:                      # optional; shell lines run in the worktree root, in order
        - "[ -d client/node_modules ] || (cd client && npm ci)"
      services:                   # name -> command; each runs under `wtg run <name>`
        api: "node server/app.js"
        web: "cd client && npm run serve -- --port $PORT"

wtg gives every service ``$PORT``, ``$WG_URL`` and ``$WG_<NAME>_URL``. The tool returns the URLs once
each service answers, or each service's log tail: the chat reports it instead of improvising.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import submit, workspace_prep

SCHEMA = {
    "type": "function",
    "function": {
        "name": "delivery_test_env",
        "description": (
            "Chat, or a verify card running the LIVE QA the user asked for: run a card's or PR's branch locally so it can be tested: the project's saved recipe, "
            "under wtg, on that card's worktree. Returns the URLs and the commit they serve, or the "
            "failing service's log. Use it whenever the user asks to run, rerun, or restart the app for "
            "testing, for any PR or branch (cpipe's or not); never start dev servers by hand and never "
            "submit a delivery just to run an app. If it reports no recipe, ask the user for the "
            "commands and do not improvise."),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Card id, PR URL, or branch name to run"},
                "project": {"type": "string", "description": "The project, needed when no card owns the PR or "
                            "branch: it gets its own detached worktree at the branch's latest pushed commit"},
            },
            "required": ["target"],
        },
    },
}

READY_SECONDS = 240


def _wtg_env(worktree: str) -> dict:
    out = subprocess.run(["wtg", "env"], cwd=worktree, capture_output=True, text=True, timeout=30).stdout
    return dict(re.findall(r"^export (\w+)='([^']*)'", out, re.M))


def _answers(url: str) -> bool:
    code = subprocess.run(["curl", "-sk", "-o", "/dev/null", "-m", "5", "-w", "%{http_code}", url],
                          capture_output=True, text=True).stdout.strip()
    return code not in ("", "000", "502", "503", "504")


def _tail(path: Path, n: int = 30) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def _branch_worktree(ref: str, project: str):
    """(worktree, error) for a PR or branch no card owns: `<repo>/.worktrees/test-<branch>`, detached at the
    branch's pushed head, so a checkout of the branch elsewhere is never touched."""
    repo = submit._project_repo(project) if project else None
    if not repo:
        return None, (f"no card owns {ref}; pass project= (the project the PR or branch belongs to)"
                      if not project else f"project {project!r} has no repository path")
    branch = ref
    if submit.URL_RE.fullmatch(ref):
        view = subprocess.run(["gh", "pr", "view", ref, "--json", "headRefName", "-q", ".headRefName"],
                              cwd=repo, capture_output=True, text=True, timeout=60)
        branch = view.stdout.strip()
        if view.returncode != 0 or not branch:
            return None, f"gh could not read {ref}: {(view.stderr or view.stdout).strip()[-300:]}"
    git = lambda *a, cwd=repo: subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True, timeout=300)
    fetched = git("fetch", "-q", "origin", branch)
    if fetched.returncode != 0:
        return None, f"git fetch origin {branch} failed: {fetched.stderr.strip()[-300:]}"
    sha = git("rev-parse", "FETCH_HEAD").stdout.strip()  # FETCH_HEAD is per worktree: pass the sha
    path = Path(repo) / ".worktrees" / ("test-" + re.sub(r"[^\w.-]+", "-", branch))
    if not path.is_dir():
        done = git("worktree", "add", "-q", "--detach", str(path), sha)
    elif not git("status", "--porcelain", "--untracked-files=no", cwd=str(path)).stdout.strip():
        done = git("checkout", "-q", "--detach", sha, cwd=str(path))
    else:
        return str(path), None  # someone changed files there: run it as it is
    return (str(path), None) if done.returncode == 0 else (None, done.stderr.strip()[-300:])


def run(args: dict, **_kw) -> str:
    ref = str(args.get("target") or "").strip()
    card = ref if re.fullmatch(r"t_[0-9a-f]+", ref) else submit._card_for_pr(ref)
    worktree = submit._card_worktree(card) if card else None
    row = submit._task_row(card) if worktree else None
    project = (row["project_id"] if row else str(args.get("project") or "").strip()) or ""
    if not worktree or not Path(worktree).is_dir():
        worktree, error = _branch_worktree(ref, project)
        if error:
            return json.dumps({"ok": False, "error": error})
    recipe = workspace_prep.project_profile(project, submit.HERMES_HOME).get("test_env") or {}
    if not recipe.get("services"):
        return json.dumps({"ok": False, "error": f"no test_env recipe for project {project!r} in "
                           "delivery/projects.yaml. Ask the user for the setup and service commands; "
                           "do not start servers by hand."})
    if not shutil.which("wtg"):
        return json.dumps({"ok": False, "error": "wtg is not installed; tell the user"})
    git = lambda *a: subprocess.run(["git", "-C", worktree, *a], capture_output=True, text=True).stdout.strip()
    for line in recipe.get("setup") or []:
        done = subprocess.run(["sh", "-c", line], cwd=worktree, capture_output=True, text=True, timeout=900)
        if done.returncode != 0:
            return json.dumps({"ok": False, "error": f"setup failed: {line}",
                               "output": (done.stderr or done.stdout)[-2000:]})
    logs = submit.HERMES_HOME / "logs" / "test-env"
    logs.mkdir(parents=True, exist_ok=True)
    env, started = _wtg_env(worktree), {}
    for name, cmd in recipe["services"].items():
        log = logs / f"{Path(worktree).name}-{name}.log"
        with log.open("w") as fh:  # `wtg run` stops this worktree's previous instance first
            subprocess.Popen(["wtg", "run", name, "--", "sh", "-c", cmd], cwd=worktree, stdout=fh,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        started[name] = (env.get(f"WG_{name.upper()}_URL") or env.get("WG_URL", ""), log)
    deadline = time.time() + int(recipe.get("ready_seconds") or READY_SECONDS)
    pending = dict(started)
    while True:
        env = _wtg_env(worktree)  # a service's URL appears in `wtg env` only once it has registered
        started = {n: (u or env.get(f"WG_{n.upper()}_URL", ""), log) for n, (u, log) in started.items()}
        pending = {n: started[n] for n in pending if not (started[n][0] and _answers(started[n][0]))}
        if not pending or time.time() >= deadline:
            break
        time.sleep(5)
    result = {"branch": git("rev-parse", "--abbrev-ref", "HEAD"), "commit": git("rev-parse", "--short", "HEAD"),
              "urls": {n: u for n, (u, _) in started.items()}}
    if pending:
        return json.dumps({"ok": False, **result, "error": f"not answering after the wait: {sorted(pending)}",
                           "log_tail": {n: _tail(log) for n, (_, log) in pending.items()}})
    return json.dumps({"ok": True, **result, "note": "servers stop by themselves after 2h (wtg)"})
