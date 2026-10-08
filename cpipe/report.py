"""``hermes cpipe report`` / ``status``: what each delivery cost: wall time, engine runs and tokens, per node."""

from __future__ import annotations

import json
from pathlib import Path


def state_of(conn, task) -> dict:
    """The delivery's state: the worktree's (newest, also mid-round), else the last run's metadata."""
    from .runtime import hermes_api
    try:
        return json.loads((Path(task.workspace_path) / ".cpipe" / "state.json").read_text())
    except (OSError, TypeError, ValueError):
        return hermes_api.latest_metadata(conn, task.id).get("cpipe") or {}


def _tokens(usage: dict) -> int:
    return int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))


def row(conn, task) -> dict:
    from .runtime import hermes_api
    st = state_of(conn, task)
    nodes: dict[str, dict] = {}
    for e in st.get("timeline") or []:
        n = nodes.setdefault(e["node"], {"seconds": 0, "tokens": 0})
        n["seconds"] += e["end"] - e["start"]
    for node, usage in (st.get("by_node") or {}).items():
        nodes.setdefault(node, {"seconds": 0, "tokens": 0})["tokens"] = _tokens(usage)
    timeline = st.get("timeline") or []
    timed_nodes = {e["node"] for e in timeline}
    for call in st.get("agent_calls") or []:
        node = call["node"]
        n = nodes.setdefault(node, {"seconds": 0, "tokens": 0})
        if node not in timed_nodes:
            n["seconds"] += call["seconds"]
    elapsed = (max(e["end"] for e in timeline) - min(e["start"] for e in timeline)) if timeline else 0
    calls = st.get("agent_calls") or []
    return {"task_id": task.id, "title": task.title, "status": task.status, "node": st.get("node"),
            "elapsed_seconds": elapsed,  # includes time between rounds and human approval waits
            "agent_seconds": sum(c["seconds"] for c in calls),  # additive work, not elapsed time
            "agent_calls": len(calls), "agents": sorted({c.get("profile") or c["engine"] for c in calls}),
            "calls_without_usage": sum(c.get("tokens") is None for c in calls),
            "engine": st.get("engine") or hermes_api.engine_of(conn, task), "pr": st.get("pr_url"),
            "runs": hermes_api.run_count(conn, task.id), "minutes": round(sum(e["end"] - e["start"] for e in timeline) / 60, 1),
            "tokens": _tokens(st.get("usage") or {}),
            "cached_input_tokens": int((st.get("usage") or {}).get("cached_input_tokens", 0)), "nodes": nodes}


def rows(conn, open_only: bool = False) -> list[dict]:
    from .runtime import hermes_api
    return [row(conn, t) for t in hermes_api.list_cards(conn)
            if not open_only or t.status not in ("done", "archived")]


def render(items: list[dict]) -> str:
    out = [f"{'card':<14} {'status':<9} {'engine':<9} {'runs':>4} {'min':>6} {'tokens':>10}  title"]
    for r in items:
        out.append(f"{r['task_id']:<14} {r['status']:<9} {r['engine']:<9} {r['runs']:>4} {r['minutes']:>6} "
                   f"{r['tokens']:>10}  {r['title'][:50]}")
        out.append("    " + "  ".join(f"{k} {v['seconds'] // 60}m/{v['tokens']}t" for k, v in r["nodes"].items()))
        out.append(f"    elapsed {r['elapsed_seconds']}s (includes waits); agent work {r['agent_seconds']}s "
                   f"across {r['agent_calls']} calls; agents {', '.join(r['agents']) or 'unrecorded'}; "
                   f"calls without token usage {r['calls_without_usage']}")
    if items:
        out.append(f"{'total':<14} {'':<9} {'':<9} {sum(r['runs'] for r in items):>4} "
                   f"{round(sum(r['minutes'] for r in items), 1):>6} {sum(r['tokens'] for r in items):>10}  "
                   f"{len(items)} deliveries")
    return "\n".join(out)
