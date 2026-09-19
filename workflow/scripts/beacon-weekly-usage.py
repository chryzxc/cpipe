#!/usr/bin/env python3
"""Beacon weekly usage digest — deterministic, no LLM.

Aggregates token usage and estimated cost for the trailing 7 days across the
Nexus state DB and every profile state DB, groups by profile and model, and
emits the 60%-before-midweek guardrail alert against the configured weekly
token budget. Run by cron (--no-agent) on Monday mornings; stdout is the digest.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
CONF = HERMES_HOME / "scripts" / "beacon-weekly-budget.conf"
WINDOW_DAYS = 7
ALERT_THRESHOLD = 0.60

DEFAULT_WEEKLY_TOKEN_BUDGET = 50_000_000  # tokens/week — edit beacon-weekly-budget.conf


def load_budget() -> int:
    budget = DEFAULT_WEEKLY_TOKEN_BUDGET
    if CONF.exists():
        for line in CONF.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "WEEKLY_TOKEN_BUDGET":
                try:
                    budget = int(value.strip())
                except ValueError:
                    pass
    return budget


def aggregate(db_path: Path, since: float) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return out
    try:
        rows = conn.execute(
            """
            SELECT model,
                   SUM(input_tokens + output_tokens + cache_read_tokens
                       + cache_write_tokens + reasoning_tokens),
                   SUM(estimated_cost_usd),
                   COUNT(*)
            FROM session_model_usage
            WHERE last_seen >= ?
            GROUP BY model
            """,
            (since,),
        ).fetchall()
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    for model, tokens, cost, calls in rows:
        if tokens:
            out[str(model)] = {"tokens": int(tokens or 0), "cost_usd": float(cost or 0.0), "calls": int(calls or 0)}
    return out


def top_sessions(db_path: Path, since: float, profile: str) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return rows
    try:
        rows = conn.execute(
            """
            SELECT u.session_id,
                   SUM(u.input_tokens + u.output_tokens + u.cache_read_tokens
                       + u.cache_write_tokens + u.reasoning_tokens) / 1000000.0
            FROM session_model_usage u
            WHERE u.last_seen >= ?
            GROUP BY u.session_id
            ORDER BY 2 DESC LIMIT 3
            """,
            (since,),
        ).fetchall()
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return [(str(s), float(m)) for s, m in rows]


def main() -> None:
    since = time.time() - WINDOW_DAYS * 86400
    budget = load_budget()

    sources: list[tuple[str, Path]] = [("nexus", HERMES_HOME / "state.db")]
    profiles_dir = HERMES_HOME / "profiles"
    if profiles_dir.is_dir():
        for entry in sorted(profiles_dir.iterdir()):
            db = entry / "state.db"
            if entry.is_dir() and db.exists():
                sources.append((entry.name, db))

    grand_total = 0
    grand_cost = 0.0
    marathon_sessions: list[tuple[str, str, float]] = []
    print(f"===== WEEKLY USAGE DIGEST (trailing {WINDOW_DAYS}d, UTC) =====")
    print(f"generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")

    for name, db_path in sources:
        per_model = aggregate(db_path, since)
        if not per_model:
            continue
        profile_tokens = sum(v["tokens"] for v in per_model.values())
        profile_cost = sum(v["cost_usd"] for v in per_model.values())
        grand_total += profile_tokens
        grand_cost += profile_cost
        print(f"\n-- {name}: {profile_tokens:,} tokens | ${profile_cost:.2f} est")
        for model, v in sorted(per_model.items(), key=lambda kv: -kv[1]["tokens"]):
            print(f"   {model}: {v['tokens']:,} tokens | {v['calls']} calls | ${v['cost_usd']:.2f}")
        for session_id, mtok in top_sessions(db_path, since, name):
            if mtok >= 100.0:
                marathon_sessions.append((name, session_id, mtok))

    pct = (grand_total / budget * 100) if budget else 0.0
    print("\n===== SESSION HYGIENE =====")
    if marathon_sessions:
        for name, session_id, mtok in marathon_sessions:
            print(f"ALERT: {name} session {session_id} burned {mtok:.0f}M tokens — marathon session, split work into per-task sessions/cards")
    else:
        print("OK: no single session exceeded 100M tokens.")

    print("\n===== BUDGET GUARDRAIL =====")
    print(f"weekly_token_budget: {budget:,}")
    print(f"trailing_7d_total:   {grand_total:,} tokens ({pct:.1f}% of budget) | ${grand_cost:.2f} est")
    if pct >= 100:
        print("ALERT: weekly budget EXCEEDED — routing review required before further non-fast-lane dispatch.")
    elif pct >= ALERT_THRESHOLD * 100:
        print(f"ALERT: above {ALERT_THRESHOLD:.0%} before budget period end — routing review recommended.")
    else:
        print("OK: below alert threshold.")
    print("raw signal only — Beacon/Human triage decides actions; empty sections are NO FINDINGS.")


if __name__ == "__main__":
    main()
