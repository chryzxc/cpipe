#!/usr/bin/env python3
"""Stall alerts — tell the operator when delivery stops moving. Deterministic, no LLM.

Runs as a no_agent cron job: stdout is delivered verbatim, empty stdout sends nothing.
Prints a digest only when a card newly stalls, a stalled card moves again, or the
periodic "still stuck" reminder is due — so silence means the board is moving.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DB = HERMES_HOME / "kanban.db"
STATE = HERMES_HOME / "state" / "stall-alert.json"
STALL_GRACE_MINUTES = 20      # no movement this long before alerting (avoids flapping)
WAITING_CLAIM_MINUTES = 15    # ready/review card nobody has picked up
SILENT_WORKER_MINUTES = 30    # running card with no heartbeat
ENGINE_WINDOW_MINUTES = 30
ENGINE_EVENT_THRESHOLD = 3
REMIND_HOURS = 12
LIST_CAP = 12
MESSAGE_BUDGET = 1600         # stay under chat limits (Discord: 2000 chars)
# Events that mean the card itself moved; comments and guard noise do not count.
QUIET_KINDS = ("heartbeat", "commented", "respawn_guarded", "claim_extended")

WHY = {
    "needs_input": "waiting on YOUR decision",
    "capability": "missing tool/env/permission, needs a fix",
    "transient": "waiting on quota/retry, should resume by itself",
}


def _short(text, n=160):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _age(seconds):
    m = int(seconds // 60)
    return f"{m}m" if m < 120 else f"{m // 60}h" if m < 2880 else f"{m // 1440}d"


def _block_reason(conn, tid):
    row = conn.execute(
        "SELECT payload FROM task_events WHERE task_id=? AND kind IN ('blocked','gave_up') "
        "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    try:
        payload = json.loads(row[0] or "{}") if row else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return payload.get("reason") or payload.get("error") or ""


def card_stalls(conn, now):
    """{key: stall} for every card that has stopped moving."""
    stalls = {}
    rows = conn.execute(f"""
        SELECT t.id, t.title, t.assignee, t.status, t.block_kind, t.consecutive_failures,
               t.last_failure_error, t.result, t.last_heartbeat_at,
               (SELECT max(created_at) FROM task_events e WHERE e.task_id=t.id
                  AND e.kind NOT IN ({",".join("?" * len(QUIET_KINDS))})) AS moved_at,
               t.created_at
        FROM tasks t WHERE t.status IN ('blocked','triage','ready','review','running')
    """, QUIET_KINDS).fetchall()
    for r in rows:
        idle = now - (r["moved_at"] or r["created_at"])
        status = r["status"]
        if status in ("blocked", "triage"):
            if idle < STALL_GRACE_MINUTES * 60:
                continue
            why = WHY.get(r["block_kind"] or "")
            if not why:
                why = ("worker keeps crashing, retries exhausted" if r["consecutive_failures"]
                       else "parked in triage, nothing dispatches it" if status == "triage"
                       else "blocked")
            detail = _block_reason(conn, r["id"]) or r["last_failure_error"] or r["result"]
        elif status in ("ready", "review"):
            if idle < WAITING_CLAIM_MINUTES * 60:
                continue
            why = f"{status} but no worker picked it up (dispatcher stuck, at capacity, or guarded)"
            detail = r["last_failure_error"]
        else:  # running
            beat = r["last_heartbeat_at"] or r["moved_at"] or r["created_at"]
            idle = now - beat
            if idle < SILENT_WORKER_MINUTES * 60:
                continue
            why = "running but the worker has gone silent (no heartbeat)"
            detail = None
        stalls[f"{r['id']}:{status}:{r['block_kind'] or ''}"] = {
            "line": f"**{_short(r['title'], 70)}** (`{r['id']}`, {r['assignee'] or 'unassigned'})"
                    f" — {status} {_age(idle)}: {why}",
            "detail": _short(detail) if detail else "",
            "since": now - idle,
            "name": _short(r["title"], 50),
        }
    return stalls


def engine_stalls(conn, now):
    """Board-wide problems that stop every card, reported once instead of per card."""
    since = now - ENGINE_WINDOW_MINUTES * 60
    stalls = {}
    crashes = conn.execute(
        "SELECT count(*), max(id) FROM task_events WHERE kind='crashed' AND created_at>=?",
        (since,)).fetchone()
    if crashes[0] >= ENGINE_EVENT_THRESHOLD:
        payload = conn.execute("SELECT payload FROM task_events WHERE id=?", (crashes[1],)).fetchone()
        try:
            tail = json.loads(payload[0] or "{}").get("worker_output", "")[-200:]
        except (json.JSONDecodeError, TypeError, AttributeError):
            tail = ""
        stalls["engine:crash"] = {
            "line": f"**Workers are crashing on start** — {crashes[0]} crashes in {ENGINE_WINDOW_MINUTES}m; "
                    "no card can progress until the launcher is fixed",
            "detail": _short(tail), "since": now, "name": "worker launcher"}
    limited = conn.execute(
        "SELECT count(*) FROM task_events WHERE kind='rate_limited' AND created_at>=?",
        (since,)).fetchone()[0]
    if limited >= ENGINE_EVENT_THRESHOLD:
        stalls["engine:quota"] = {
            "line": f"**Model quota wall** — {limited} runs rate-limited in {ENGINE_WINDOW_MINUTES}m; "
                    "work is paused until the provider quota resets",
            "detail": "", "since": now, "name": "model quota"}
    return stalls


def roster_stalls(now):
    """A required role with no working bot stops every card routed to it."""
    try:
        from roster_gaps import fix_hint, roster_gaps  # same dir: ~/.hermes/scripts or the repo
        gaps = roster_gaps(home=HERMES_HOME)
    except (ImportError, OSError):
        return {}
    return {f"roster:{role}": {
        "line": f"**Role `{role}` has no working bot** ({why}) — cards for this role can't start",
        "detail": f"Fix: {fix_hint(role)}", "since": now, "name": f"role {role}"}
        for role, why in gaps.items()}


def build_digest(current, state, now):
    """Return (message, new_state). Message is '' when there is nothing new to say."""
    seen = state.get("seen", {})
    new = [k for k in current if k not in seen]
    resolved = [k for k in seen if k not in current]
    remind = bool(current) and not new and now - state.get("reminded_at", 0) >= REMIND_HOURS * 3600
    lines = []
    if new:
        lines.append(f"⚠️ **Delivery stalled — {len(new)} new**")
        new.sort(key=lambda k: (not k.startswith(("roster:", "engine:")), -current[k]["since"]))
        shown = 0
        for k in new:
            item = f"• {current[k]['line']}" + (f"\n  ↳ {current[k]['detail']}" if current[k]["detail"] else "")
            if shown == LIST_CAP or sum(map(len, lines)) + len(item) > MESSAGE_BUDGET:
                break
            lines.append(item)
            shown += 1
        if len(new) > shown:
            lines.append(f"…and {len(new) - shown} more — `hermes kanban list --status blocked`")
    if resolved:
        names = ", ".join(seen[k].get("name", k) for k in resolved[:5])
        more = f" (+{len(resolved) - 5} more)" if len(resolved) > 5 else ""
        lines.append(f"✅ Moving again: {names}{more}")
    if current and (new or remind):
        oldest = min(v["since"] for v in current.values())
        lines.append(f"⏳ Stuck overall: {len(current)} item(s), oldest {_age(now - oldest)}")
    new_state = {
        "seen": {k: {"name": v["name"]} for k, v in current.items()},
        "reminded_at": now if (new or remind) else state.get("reminded_at", 0),
    }
    return "\n".join(lines), new_state


def main():
    if not DB.exists():
        return
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = time.time()
    current = {**roster_stalls(now), **engine_stalls(conn, now), **card_stalls(conn, now)}
    conn.close()
    try:
        state = json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    message, new_state = build_digest(current, state, now)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(new_state))
    if message:
        print(message)


if __name__ == "__main__":
    main()
