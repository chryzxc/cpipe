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
            "Chat only: run a card's or PR's branch locally so the user can test it: the project's saved recipe, "
            "under wtg, on that card's worktree. Returns the URLs and the commit they serve, or the "
            "failing service's log. Use it whenever the user asks to run, rerun, or restart the app for "
            "testing; never start dev servers by hand. If it reports no recipe, ask the user for the "
            "commands and do not improvise."),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Card id or PR URL whose branch to run"},
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


def run(args: dict, **_kw) -> str:
    ref = str(args.get("target") or "").strip()
    card = ref if re.fullmatch(r"t_[0-9a-f]+", ref) else submit._card_for_pr(ref)
    worktree = submit._card_worktree(card) if card else None
    if not worktree or not Path(worktree).is_dir():
        return json.dumps({"ok": False, "error": f"no live worktree found for {ref}"})
    row = submit._task_row(card)
    project = (row["project_id"] if row else "") or ""
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
