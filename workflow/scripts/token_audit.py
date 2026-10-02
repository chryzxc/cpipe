#!/usr/bin/env python3
"""Token-efficiency audit — deterministic, no LLM.

Where the tokens went over a window, and the habits behind them: per-bot share and context per call,
per-delivery cost, coordinator skill loads / comments / repo work, compactions, worker re-reads.
Each run saves a snapshot to $HERMES_HOME/audits/ and prints the change against the previous one.

  token_audit.py [--hours 24] [--json]

Session totals are lifetime counters, so a session's tokens are scaled by the share of its assistant
turns inside the window (an estimate; good enough to rank).
"""

from __future__ import annotations

import collections
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
AUDITS = HOME / "audits"
COORDINATOR = "nexus"  # the root profile (state.db at HERMES_HOME)
REPO_TOOLS = ("read_file", "search_files", "terminal", "execute_code")


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _delivery(title: str) -> str:
    return re.sub(r"^((Verify|Review|Fix \d+|Build|Plan|Map):\s*)+", "", title or "")[:60] or "?"


def collect(hours: float) -> dict:
    since = time.time() - hours * 3600
    bots: dict = collections.defaultdict(lambda: {"tokens": 0.0, "calls": 0.0})
    deliveries: collections.Counter = collections.Counter()
    skills: collections.Counter = collections.Counter()
    tools: collections.Counter = collections.Counter()
    compactions: collections.Counter = collections.Counter()
    reads = rereads = 0
    for db in [HOME / "state.db", *sorted(HOME.glob("profiles/*/state.db"))]:
        bot = COORDINATOR if db.parent == HOME else db.parent.name
        try:
            c = _ro(db)
            sessions = c.execute(
                "SELECT id, source, COALESCE(input_tokens,0)+COALESCE(output_tokens,0)+COALESCE(cache_read_tokens,0)"
                "+COALESCE(cache_write_tokens,0), COALESCE(api_call_count,0), COALESCE(title,'') FROM sessions "
                "WHERE COALESCE(last_activity_at, started_at) >= ?", (since,)).fetchall()
        except sqlite3.Error:
            continue
        for sid, source, total, calls, title in sessions:
            n, w = c.execute("SELECT COUNT(*), SUM(timestamp >= ?) FROM messages WHERE session_id=? AND role='assistant'",
                             (since, sid)).fetchone()
            share = (w or 0) / n if n else 1.0
            bots[bot]["tokens"] += total * share
            bots[bot]["calls"] += calls * share
            if source == "kanban":
                deliveries[_delivery(title)] += total * share
            compactions[bot] += c.execute(
                "SELECT COUNT(DISTINCT timestamp) FROM messages WHERE session_id=? AND timestamp >= ? AND role='user' "
                "AND content LIKE '[CONTEXT COMPACTION%'", (sid, since)).fetchone()[0]
            seen: set = set()
            for (raw,) in c.execute("SELECT DISTINCT tool_calls FROM messages WHERE session_id=? AND timestamp >= ? "
                                    "AND role='assistant' AND tool_calls IS NOT NULL", (sid, since)):
                try:
                    calls_ = json.loads(raw)
                except ValueError:
                    continue
                for call in calls_:
                    fn = (call.get("function") or {})
                    name = fn.get("name", "?")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    if bot == COORDINATOR:
                        tools[name] += 1
                        if name == "skill_view":
                            skills[args.get("name", "?")] += 1
                    elif name == "read_file" and isinstance(args, dict):
                        key = (args.get("path"), args.get("offset"), args.get("limit"))
                        reads += 1
                        rereads += key in seen
                        seen.add(key)
    try:
        k = _ro(HOME / "kanban.db")
        comments = k.execute("SELECT author, COUNT(*), COALESCE(AVG(LENGTH(body)),0) FROM task_comments "
                             "WHERE created_at >= ? GROUP BY author", (since,)).fetchall()
        fixes = k.execute("SELECT COUNT(*) FROM tasks WHERE created_at >= ? AND title LIKE 'Fix %'", (since,)).fetchone()[0]
        round_limits = k.execute("SELECT COUNT(*) FROM task_events WHERE created_at >= ? AND kind='blocked' "
                                 "AND payload LIKE '%round limit%'", (since,)).fetchone()[0]
    except sqlite3.Error:
        comments, fixes, round_limits = [], 0, 0
    by_author = {a: {"n": n, "avg_chars": round(l)} for a, n, l in comments}  # Nexus comments as "default"
    total = sum(b["tokens"] for b in bots.values()) or 1
    coord = bots.get(COORDINATOR, {"tokens": 0, "calls": 0})
    return {
        "at": int(time.time()), "hours": hours, "total_tokens": round(total),
        "bots": {b: {"tokens": round(v["tokens"]), "share": round(100 * v["tokens"] / total, 1),
                     "calls": round(v["calls"]), "per_call": round(v["tokens"] / v["calls"]) if v["calls"] else 0}
                 for b, v in sorted(bots.items(), key=lambda x: -x[1]["tokens"]) if v["tokens"] >= 1},
        "deliveries": {d: round(t) for d, t in deliveries.most_common(8)},
        "coordinator": {
            "share": round(100 * coord["tokens"] / total, 1),
            "skill_loads": dict(skills.most_common(6)), "skill_loads_total": sum(skills.values()),
            "repo_tool_calls": sum(tools[t] for t in REPO_TOOLS),
            "comments": by_author.get("default", {}).get("n", 0),
            "comment_avg_chars": by_author.get("default", {}).get("avg_chars", 0),
        },
        "compactions": dict(compactions.most_common()),
        "worker_reread_pct": round(100 * rereads / reads, 1) if reads else 0.0,
        "fix_cards": fixes, "round_limit_blocks": round_limits,
        "comments_by_author": by_author,
    }


def _flat(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}."))
        elif isinstance(v, (int, float)) and k not in ("at", "hours"):
            out[f"{prefix}{k}"] = v
    return out


def render(snap: dict, prev: dict | None) -> str:
    M = lambda t: f"{t / 1e6:.1f}M"
    lines = [f"TOKEN AUDIT — last {snap['hours']:g}h — ~{M(snap['total_tokens'])} tokens (estimated)", "", "Bots:"]
    lines += [f"  {b:10} {M(v['tokens']):>7} {v['share']:5.1f}%  calls={v['calls']:<5} per_call={v['per_call'] // 1000}K"
              for b, v in snap["bots"].items()]
    lines += ["", "Deliveries (worker tokens):"] + [f"  {M(t):>7} {d}" for d, t in snap["deliveries"].items()]
    c = snap["coordinator"]
    lines += ["", f"Coordinator: share={c['share']}%  skill_loads={c['skill_loads_total']} {c['skill_loads']}",
              f"  repo tool calls={c['repo_tool_calls']}  comments={c['comments']} avg {c['comment_avg_chars']} chars",
              f"Compactions: {snap['compactions']}",
              f"Worker re-reads: {snap['worker_reread_pct']}%   fix cards: {snap['fix_cards']}   "
              f"round-limit blocks: {snap['round_limit_blocks']}"]
    if prev:
        a, b = _flat(prev), _flat(snap)
        moved = [(k, a[k], b[k]) for k in sorted(set(a) & set(b))
                 if not k.startswith(("deliveries.", "comments_by_author.")) and a[k] != b[k]
                 and abs(b[k] - a[k]) >= max(1, 0.1 * abs(a[k]))]
        when = time.strftime("%m-%d %H:%M", time.localtime(prev["at"]))
        lines += ["", f"Change vs previous audit ({when}, {prev['hours']:g}h):"]
        lines += [f"  {k}: {x:g} → {y:g}" for k, x, y in moved] or ["  no material change"]
    return "\n".join(lines)


def main() -> None:
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 24.0
    snap = collect(hours)
    AUDITS.mkdir(exist_ok=True)
    previous = sorted(AUDITS.glob("audit-*.json"))
    prev = json.loads(previous[-1].read_text()) if previous else None
    (AUDITS / f"audit-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(snap, indent=1))
    print(json.dumps(snap, indent=1) if "--json" in sys.argv else render(snap, prev))


if __name__ == "__main__":
    main()
