"""``python -m cpipe.round <task> --result <file>``: one round of a delivery, launched by the runtime's ``round``
adapter in the card's worktree. Walks ``graph`` from the card's current node to an interrupt, then writes
``{status, summary, metadata}`` for the supervisor to apply (review / block / requeue).

State lives in ``.cpipe/state.json`` in the worktree (git-excluded), so it survives across rounds; a copy goes
into the card's metadata at every handoff. New input arrives as card comments (from anyone but cpipe).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Optional

from . import engines, graph, quality, settings, workspace_prep
from .runtime import hermes_api

ME = "cpipe"


def log(msg: str) -> None:
    print(f"[cpipe {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def state_path(ws: Path) -> Path:
    return ws / ".cpipe" / "state.json"


def load_state(ws: Path) -> Optional[dict]:
    try:
        return json.loads(state_path(ws).read_text())
    except (OSError, ValueError):
        return None


def save_state(ws: Path, state: dict) -> None:
    state_path(ws).parent.mkdir(exist_ok=True)
    state_path(ws).write_text(json.dumps(state, indent=1))


def new_state(ws: Path, task, engine: str, cfg: dict) -> dict:
    project = workspace_prep.project_of(ws)
    profile = workspace_prep.project_profile(project)
    return {"v": 1, "project": project, "base": profile.get("base_branch") or "main", "engine": engine,
            "model": (cfg["engines"].get(engine) or {}).get("model"), "node": "setup", "session": None,
            "last_comment_id": 0, "assumptions": [], "usage": {}, "timeline": [], "request": task.title}


def build_prompt(task, ws: Path, state: dict, notes: list[str]) -> str:
    profile = workspace_prep.project_profile(state["project"])
    conventions = "\n".join(str(profile.get("conventions") or "").strip().splitlines()[:10])
    lessons = workspace_prep.lessons(state["project"])
    return "\n\n".join(filter(None, [
        f"{task.title}\n\n{(task.body or '').strip()}".strip(),
        f"Worktree {ws}, branch {task.branch_name}, PR base {state['base']}. Project: {state['project']}.",
        "- Do the whole task in this session: read what you need, edit, add/adjust tests that prove it, run them, "
        "commit (Conventional Commits) on this branch. For large tasks keep .cpipe/progress.md current.\n"
        "- Don't push, open PRs, merge, deploy, or touch production/credentials: the runner does that.\n"
        "- Don't stop to ask. Ambiguity → choose what a careful senior would and list it in `assumptions`. "
        "`needs_user` only for destructive, external, security or credential decisions.\n"
        "- Minimal change matching surrounding code; reuse existing helpers. Follow AGENTS.md.",
        conventions,
        "Lessons from past deliveries:\n" + "\n".join(f"- {t}" for t in lessons) if lessons else "",
        "\n".join(f"Note: {n}" for n in notes),
    ]))


def feedback(conn, task_id: str, state: dict) -> str:
    """New comments since the last round, from anyone but cpipe; advances ``last_comment_id``."""
    new = hermes_api.comments_after(conn, task_id, state["last_comment_id"])
    if new:
        state["last_comment_id"] = new[-1].id
    return "\n\n".join(f"{c.author}: {c.body.strip()}" for c in new if c.author != ME)


def run_round(task_id: str, ws: Path, conn, engine: str, cfg: dict) -> dict:
    task = hermes_api.get_task(conn, task_id)
    state = load_state(ws) or new_state(ws, task, engine, cfg)
    limits, rounds = cfg["limits"], {}
    eng, eng_cfg = engines.get(engine), cfg["engines"].get(engine) or {}
    said = feedback(conn, task_id, state)
    if state["session"]:
        state["node"] = "build"
        prompt = (f"New input from the user on this delivery:\n{said}\n\nAddress it, test, commit." if said
                  else "Continue from the last commit and finish the task.")
    else:
        state["node"], prompt = "setup", ""
    result: Optional[engines.EngineResult] = None
    while state["node"] not in graph.TERMINAL:
        node, start = state["node"], time.time()
        log(f"node {node}")
        if node == "setup":
            notes = workspace_prep.prepare(ws, state["base"])
            prompt, outcome = build_prompt(task, ws, state, notes) + (f"\n\nAlso from the user:\n{said}" if said else ""), "ok"
        elif node == "build":
            result = eng.run(eng_cfg, prompt, ws, session=state["session"], log=log)
            state["session"] = result.session or state["session"]
            state["assumptions"] += result.data.get("assumptions") or []
            for k, v in result.tokens.items():
                state["usage"][k] = state["usage"].get(k, 0) + v
            outcome = "needs_user" if result.status == "needs_user" else "ok" if result.status == "done" else result.status
        else:  # checks
            failures = quality.run_checks(ws, quality.diff_base(ws, state["base"]) or f"origin/{state['base']}",
                                          state["project"], card=task_id)
            outcome = "fail" if failures else "ok"
            if failures:
                rounds["checks"] = rounds.get("checks", 0) + 1
                prompt = "The checks failed on your branch:\n\n" + "\n\n".join(failures) + "\n\nFix it and commit."
                log(f"checks failed ({rounds['checks']}/{limits['checks']})")
        state["timeline"].append({"node": node, "start": int(start), "end": int(time.time()), "outcome": outcome})
        state["node"] = graph.next_node(node, outcome, rounds, limits)
        save_state(ws, state)
    end = state["node"]
    status = graph.TERMINAL[end]
    summary = {
        "handoff": (result.summary if result else "") or "Built and checked.",
        "ask": (result.data.get("question") if result else "") or "The engine needs a decision.",
        "stuck": f"The checks still fail after {limits['checks']} fixes:\n{prompt[:3000]}",
    }.get(end, result.summary if result else end)
    return {"status": status, "summary": summary, "metadata": {"cpipe": state}}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="cpipe.round")
    ap.add_argument("task_id")
    ap.add_argument("--result", required=True)
    args = ap.parse_args(argv)
    ws = Path(os.environ.get("WR_WORKSPACE") or os.getcwd())
    conn = hermes_api.connect(os.environ.get("WR_BOARD") or None)
    cfg = settings.load()
    engine = os.environ.get("WR_LANE") or cfg["default_engine"]
    out = run_round(args.task_id, ws, conn, engine, cfg)
    log(f"round ends: {out['status']}: {out['summary'][:300]}")
    Path(args.result).write_text(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
