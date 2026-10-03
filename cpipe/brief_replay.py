"""Replay past Map/Plan cards with today's briefs: did a brief change make the same task cheaper?

    python3 -m cpipe.brief_replay --stage plan --limit 2 [--dry-run]

Each past done card is re-run on its own profile (same model, SOUL, plugins) in a detached worktree at the
commit the card started from, with the brief submit.py writes now. Prints old vs new tool calls, tokens and
cost. Only the read-only stages (map, plan): an implementer or verifier replay would push branches and PRs.
Runs spend real quota; --dry-run only compares brief sizes.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from . import submit, workspace_prep

STAGES = {"map": ("Map: ", "investigator"), "plan": ("Plan: ", "planner")}
COLS = "tool_call_count, input_tokens + cache_read_tokens, output_tokens, estimated_cost_usd"


def cards(stage: str, limit: int, ids: list[str] | None = None) -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{submit._db()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if ids:
            return conn.execute(f"SELECT * FROM tasks WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        return conn.execute("SELECT * FROM tasks WHERE status = 'done' AND title LIKE ? AND body LIKE "
                            "'%REQUEST (from the user):%' ORDER BY completed_at DESC LIMIT ?",
                            (STAGES[stage][0] + "%", limit)).fetchall()
    finally:
        conn.close()


def new_brief(stage: str, card) -> str:
    """The card body today's submit.py writes for the card's REQUEST."""
    title = card["title"].removeprefix(STAGES[stage][0])
    request = submit._request_of(card["body"])
    if stage == "map":
        argv = submit.build_card(title, request, card["project_id"] or "", "large")[0]
    else:
        argv = submit.chained_card("plan", title, request, card["project_id"] or "", "replay")[0]
    if "BUG REPORT" in (card["body"] or ""):
        argv = submit._as_bug(argv, stage)
    argv = submit._stamp(argv, submit._conventions(card["project_id"] or ""))
    return argv[argv.index("--body") + 1]


def worker_prompt(stage: str, card) -> str:
    """What a worker sees: title, body, and the parent handoff."""
    handoff = _parent_results(card["id"])
    return f"# {card['title']}\n\n{new_brief(stage, card)}" + (f"\n\n## Parent task results\n{handoff}" if handoff else "")


def _parent_results(tid: str) -> str:
    conn = sqlite3.connect(f"file:{submit._db()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT COALESCE((SELECT summary FROM task_runs r WHERE r.task_id = t.id AND outcome = 'completed' "
            "ORDER BY started_at DESC LIMIT 1), t.result) FROM tasks t JOIN task_links l ON l.parent_id = t.id "
            "WHERE l.child_id = ? AND t.status = 'done'", (tid,)).fetchall()
    finally:
        conn.close()
    return "\n\n".join(r[0] for r in rows if r[0])


def sessions(profile: str, where: str, args: tuple) -> dict:
    db = submit.HERMES_HOME / "profiles" / profile / "state.db"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(f"SELECT {COLS} FROM sessions WHERE {where}", args).fetchall()
    finally:
        conn.close()
    tools, tokens_in, tokens_out, cost = (sum(r[i] or 0 for r in rows) for i in range(4))
    return {"sessions": len(rows), "tools": tools, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost": round(cost, 2)}


def replay(stage: str, card, timeout: int) -> dict:
    profile = card["assignee"]
    repo = (card["workspace_path"] or "").split("/.worktrees/")[0]
    base = subprocess.run(["git", "-C", repo, "rev-list", "-1", f"--before={card['created_at']}", "origin/HEAD"],
                          capture_output=True, text=True).stdout.strip()
    if not base:
        return {"error": f"no base commit in {repo or 'unknown repo'}"}
    ws = Path(tempfile.mkdtemp(prefix=f"replay-{card['id']}-"))
    subprocess.run(["git", "-C", repo, "worktree", "add", "--detach", str(ws), base], capture_output=True, check=True)
    try:
        workspace_prep.link_codegraph(ws)
        prompt = ws.parent / f"{ws.name}.prompt"
        prompt.write_text(worker_prompt(stage, card))
        env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_KANBAN")}
        t0 = time.time()
        try:
            subprocess.run(["hermes", "-p", profile, "chat", "-Q", "--query-file", str(prompt)], cwd=ws, env=env,
                           capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            pass
        return {"seconds": round(time.time() - t0), **sessions(profile, "started_at >= ?", (t0,))}
    finally:
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", str(ws)], capture_output=True)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--stage", choices=STAGES, default="plan")
    p.add_argument("--limit", type=int, default=2)
    p.add_argument("--cards", nargs="*", help="replay these card ids instead of the newest done ones")
    p.add_argument("--timeout", type=int, default=1800, help="seconds per replay")
    p.add_argument("--dry-run", action="store_true", help="compare brief sizes only; spends nothing")
    a = p.parse_args(argv)
    for card in cards(a.stage, a.limit, a.cards):
        row = {"card": card["id"], "title": card["title"][:60],
               "brief_chars": {"old": len(card["body"] or ""), "new": len(new_brief(a.stage, card))},
               "old": sessions(card["assignee"], "title = ? AND source = 'kanban'", (card["title"],))}
        if not a.dry_run:
            row["new"] = replay(a.stage, card, a.timeout)
        print(json.dumps(row))


if __name__ == "__main__":
    main()
