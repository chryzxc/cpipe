#!/usr/bin/env python3
"""Board triage — sort stuck cards, retry the ones that only hit a quota, remind the operator.
Deterministic, no LLM.

Runs as a no_agent cron job (stdout is delivered verbatim, empty stdout sends nothing):
  * blocked cards that only hit a rate limit/quota/timeout are unblocked (max 2 retries a card,
    and never while the provider is still rate limiting);
  * blocked cards with no reason whose parent is still open are unblocked so they wait in todo;
  * once a day it sends a digest of what needs the operator, grouped, with reply hints.
Mission Control (the dashboard tab) imports ``classify`` from here, so both show the same groups.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DB = HERMES_HOME / "kanban.db"
STATE = HERMES_HOME / "state" / "board-triage.json"
STUCK = ("blocked", "triage", "scheduled")
MAX_RETRIES = 2
QUOTA_WINDOW_MINUTES = 30
QUOTA_EVENTS = 3              # this many rate_limited events in the window = provider still walled
REMIND_HOURS = 24
PER_GROUP = 3
MESSAGE_BUDGET = 1600         # stay under chat limits (Discord: 2000 chars)
DASHBOARD_URL = os.environ.get("HERMES_DASHBOARD_URL", "http://127.0.0.1:9119/mission-control")

# First match wins; order matters ("already merged" can also mention a timeout).
RULES = [
    ("done", re.compile(r"already (merged|present|contains|implemented|done|fixed|landed)|superseded|duplicate|"
                        r"taking over|replacement|verdict is APPROVED|are green|nothing (left )?to (do|change)", re.I)),
    ("retry", re.compile(r"\b429\b|rate.?limit|quota|RateLimitError|timed? ?out|timeout|overloaded|"
                         r"elapsed \d+s > limit|protocol violation", re.I)),
    ("stale", re.compile(r"not a git repo|scratch|workspace|node_modules|\bOCR\b|npm ci|lockfile|"
                         r"UNSUBSCRIBED|MongoDB is unavailable|cannot run", re.I)),
]
GROUPS = {  # key -> (heading, what the operator does)
    "decide": ("Needs your decision", "reply `continue <id> <decision>`"),
    "done": ("Looks done or superseded", "confirm with `archive <id>`"),
    "stale": ("Blocked by old rules or environment", "`resubmit <id>` starts it fresh under the new flow"),
    "parked": ("Parked with no reason", "`continue <id>` or `archive <id>`"),
    "retry": ("Hit a rate limit or timeout", "blocked ones retry by themselves; `continue <id>` the ones listed"),
    "waiting": ("Waiting on a parent card", ""),
}
REASON_KINDS = ("blocked", "gave_up", "block_loop_detected", "scheduled")


def _short(text, n=120):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _age(seconds):
    m = int(seconds // 60)
    return f"{m}m" if m < 120 else f"{m // 60}h" if m < 2880 else f"{m // 1440}d"


def _reason(conn, tid):
    row = conn.execute(f"SELECT payload FROM task_events WHERE task_id=? AND kind IN "
                       f"({','.join('?' * len(REASON_KINDS))}) ORDER BY id DESC LIMIT 1",
                       (tid, *REASON_KINDS)).fetchone()
    try:
        payload = json.loads(row[0] or "{}") if row else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return payload.get("reason") or payload.get("error") or ""


def _since(conn, tid, fallback):
    row = conn.execute("SELECT max(created_at) FROM task_events WHERE task_id=? AND kind NOT IN "
                       "('heartbeat','commented','respawn_guarded','claim_extended')", (tid,)).fetchone()
    return (row and row[0]) or fallback


def classify(conn, now=None):
    """Every stuck card as a dict with a ``group`` key (see GROUPS), oldest first."""
    now = now or time.time()
    rows = conn.execute(f"SELECT id, title, status, assignee, block_kind, created_at FROM tasks "
                        f"WHERE status IN ({','.join('?' * len(STUCK))})", STUCK).fetchall()
    cards = []
    for tid, title, status, assignee, kind, created in rows:
        reason = _reason(conn, tid)
        open_parents = [p for (p,) in conn.execute(
            "SELECT t.id FROM task_links l JOIN tasks t ON t.id = l.parent_id "
            "WHERE l.child_id=? AND t.status NOT IN ('done','archived')", (tid,))]
        group = next((g for g, rx in RULES if rx.search(reason)), None)
        if group is None:
            if kind == "transient":
                group = "retry"
            elif kind == "capability":
                group = "stale"
            elif not reason or kind == "dependency":
                group = "waiting" if open_parents else "parked"
            else:
                group = "decide"
        since = _since(conn, tid, created)
        cards.append({"id": tid, "title": title, "status": status, "assignee": assignee or "",
                      "block_kind": kind or "", "reason": reason, "group": group,
                      "open_parents": open_parents, "since": since, "age": _age(now - since)})
    cards.sort(key=lambda c: c["since"])
    return cards


def provider_walled(conn, now):
    since = now - QUOTA_WINDOW_MINUTES * 60
    (n,) = conn.execute("SELECT count(*) FROM task_events WHERE kind='rate_limited' AND created_at>=?",
                        (since,)).fetchone()
    return n >= QUOTA_EVENTS


def hermes(*args):
    exe = os.environ.get("HERMES_BIN") or shutil.which("hermes") or str(Path.home() / ".local/bin/hermes")
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return out.returncode == 0, (out.stdout or out.stderr).strip()


def auto_fix(cards, state, walled, run=hermes):
    """Unblock what is safe to unblock. Returns (retried ids, requeued-to-wait ids)."""
    retries = state.setdefault("retries", {})
    retried, waiting = [], []
    for c in cards:
        if c["status"] != "blocked":
            continue  # unblock only moves blocked/scheduled; triage and schedules are the operator's
        if c["group"] == "retry" and not walled and retries.get(c["id"], 0) < MAX_RETRIES:
            if run("kanban", "unblock", c["id"], "--reason", "auto-retry: only hit a rate limit/timeout")[0]:
                retries[c["id"]] = retries.get(c["id"], 0) + 1
                retried.append(c["id"])
        elif c["group"] == "waiting":
            if run("kanban", "unblock", c["id"], "--reason", "no blocker recorded; waiting on parent in todo")[0]:
                waiting.append(c["id"])
    return retried, waiting


def build_digest(cards, retried, waiting, state, now):
    """Return (message, new_state). Daily, or sooner when a new decision is needed."""
    decide = {c["id"] for c in cards if c["group"] == "decide"}
    new_decide = decide - set(state.get("decide", []))
    due = now - state.get("reminded_at", 0) >= REMIND_HOURS * 3600
    new_state = {**state, "decide": sorted(decide)}
    if not (due or new_decide) or not cards:
        return "", new_state
    new_state["reminded_at"] = now
    lines = [f"📋 **Board check — {len(cards)} card(s) not moving** · Mission Control: {DASHBOARD_URL}"]
    moved = [f"retried {len(retried)}" if retried else "", f"{len(waiting)} back to wait on parents" if waiting else ""]
    if any(moved):
        lines.append("Auto: " + ", ".join(m for m in moved if m))
    footer = "\nReply: `continue t_x <what to do>` · `archive t_x` · `resubmit t_x`"
    budget = MESSAGE_BUDGET - len(footer)
    for key, (heading, hint) in GROUPS.items():
        group = [c for c in cards if c["group"] == key]
        if key == "retry":
            group = [c for c in group if c["status"] != "blocked"]  # blocked ones the cron retries itself
        if not group:
            continue
        head = f"\n**{heading} ({len(group)})**" + (f" — {hint}" if hint else "")
        if key == "waiting" or sum(map(len, lines)) + len(head) > budget:
            lines.append(head) if key == "waiting" else None
            continue
        lines.append(head)
        shown = 0
        for c in group[:PER_GROUP]:
            item = f"• `{c['id']}` {_short(c['title'], 60)} · {c['age']}" + (
                f"\n  ↳ {_short(c['reason'], 100)}" if c["reason"] else "")
            if sum(map(len, lines)) + len(item) + 80 > budget:
                break
            lines.append(item)
            shown += 1
        if len(group) > shown:
            lines.append(f"  …+{len(group) - shown} more in Mission Control")
    lines.append(footer)
    return "\n".join(lines), new_state


def main():
    if not DB.exists():
        return
    now = time.time()
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cards, walled = classify(conn, now), provider_walled(conn, now)
    conn.close()
    try:
        state = json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    retried, waiting = auto_fix(cards, state, walled)
    moved = set(retried) | set(waiting)
    message, new_state = build_digest([c for c in cards if c["id"] not in moved], retried, waiting, state, now)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(new_state))
    if message:
        print(message)


if __name__ == "__main__":
    main()
