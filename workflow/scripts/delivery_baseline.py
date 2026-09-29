#!/usr/bin/env python3
"""Delivery baseline — the autonomy targets (docs/plans, section 3) measured over the last N days.

Read-only. Prints a table and records one ``baseline`` journal line, so each change is compared
with the numbers before it:

  python3 delivery_baseline.py [days=7]
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent))
import delivery_journal as journal  # noqa: E402

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
SUPERVISOR_SCRIPT = "kanban-supervisor-scan.py"


def board_numbers(conn, since: float) -> dict:
    (completed,) = conn.execute("SELECT count(*) FROM task_runs WHERE outcome IN ('completed','review_requested') "
                                "AND ended_at >= ?", (since,)).fetchone()
    (spawns,) = conn.execute("SELECT count(*) FROM task_events WHERE kind='spawned' AND created_at >= ?",
                             (since,)).fetchone()
    (max_runs,) = conn.execute("SELECT coalesce(max(n), 0) FROM (SELECT count(*) n FROM task_runs "
                               "WHERE started_at >= ? GROUP BY task_id)", (since,)).fetchone()
    (over_budget,) = conn.execute("SELECT count(*) FROM (SELECT task_id FROM task_runs WHERE started_at >= ? "
                                  "GROUP BY task_id HAVING count(*) > 10)", (since,)).fetchone()
    (stale,) = conn.execute(
        "SELECT count(*) FROM tasks t WHERE status IN ('blocked','triage') AND coalesce((SELECT max(created_at) "
        "FROM task_events e WHERE e.task_id=t.id AND kind NOT IN ('heartbeat','commented','respawn_guarded',"
        "'claim_extended')), t.created_at) < ?", (time.time() - 7 * 86400,)).fetchone()
    (rate_limited,) = conn.execute("SELECT count(*) FROM task_runs WHERE outcome='rate_limited' AND ended_at >= ?",
                                   (since,)).fetchone()
    (guarded,) = conn.execute("SELECT count(*) FROM task_events WHERE kind='respawn_guarded' AND created_at >= ?",
                              (since,)).fetchone()
    return {"spawns_per_completed_run": round(spawns / completed, 2) if completed else None,
            "max_runs_on_one_card": max_runs, "cards_over_run_budget": over_budget,
            "undecided_blocked_over_7d": stale, "rate_limited_runs": rate_limited,
            "respawn_guarded_events": guarded, "completed_runs": completed}


def journal_numbers(since: float, home: Path) -> dict:
    nudges, stuck_at, latencies, delivered = 0, {}, [], set()
    for e in journal.read(since, home=home):
        kind, card = e.get("kind"), e.get("card")
        if kind == "nudge":
            nudges += 1
        elif kind == "monitor.state" and str(e.get("state", "")).startswith("STUCK"):
            stuck_at.setdefault(card, e["ts"])
        elif kind in ("monitor.repair", "monitor.escalate") and card in stuck_at:
            latencies.append(e["ts"] - stuck_at.pop(card))
        elif kind == "card.outcome" and e.get("outcome") == "done":
            delivered.add(e.get("chain") or card)
    return {"nudges": nudges, "nudges_per_delivered_card": round(nudges / len(delivered), 2) if delivered else None,
            "stuck_to_action_median_min": round(median(latencies) / 60, 1) if latencies else None,
            "stuck_never_acted_on": len(stuck_at)}


def supervisor_llm_share(since: float, home: Path) -> float | None:
    """Share of supervisor ticks that reached the model: agent outputs / scheduled ticks."""
    try:
        jobs = json.loads((home / "cron" / "jobs.json").read_text())
        jobs = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
        job = next(j for j in jobs if j.get("monitor_script") == SUPERVISOR_SCRIPT)
    except (OSError, ValueError, StopIteration):
        return None
    expr = (job.get("schedule") or {}).get("expr", "")
    every = int(expr.split()[0].removeprefix("*/")) if expr.startswith("*/") else 15
    ticks = (time.time() - since) / (every * 60)
    runs = [p for p in (home / "cron" / "output" / job["id"]).glob("*.md") if p.stat().st_mtime >= since]
    return round(len(runs) / ticks, 2) if ticks else None


def main():
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 7
    since = time.time() - days * 86400
    conn = sqlite3.connect(f"file:{HERMES_HOME / 'kanban.db'}?mode=ro", uri=True)
    try:
        numbers = {**board_numbers(conn, since), **journal_numbers(since, HERMES_HOME),
                   "supervisor_llm_share": supervisor_llm_share(since, HERMES_HOME)}
    finally:
        conn.close()
    journal.record("baseline", None, home=HERMES_HOME, days=days, **numbers)
    width = max(map(len, numbers))
    print(f"delivery baseline, last {days:g} days")
    for key, value in numbers.items():
        print(f"  {key:<{width}}  {'n/a' if value is None else value}")


if __name__ == "__main__":
    main()
