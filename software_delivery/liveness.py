"""Dispatch liveness: engine tick telemetry, stall notices, and doctor facts.

The gateway's embedded dispatcher decides every spawn, but its reasons for
holding work back (per-profile cap, respawn guard, memory pressure, global
ESTOP) only reach ``gateway.log``. This module turns them into durable,
board-adjacent facts:

* ``record_dispatch_tick`` runs on ``on_kanban_dispatch_tick`` (gateway process)
  and keeps ``$HERMES_HOME/logs/dispatch-health.json`` — last tick, last spawn,
  and a per-card hold reason with a ``since`` timestamp. The stall scan reads it
  to tell "queued at capacity" from "stuck".
* ``liveness_notice`` feeds ``pre_llm_call``: when ESTOP is engaged with waiting
  work, or the dispatcher has stopped ticking, the next session turn is told so
  the operator hears it without asking. ESTOP also pauses cron — including the
  stall supervisor — so a chat turn is the only channel still open.

Every entry point is best-effort and never raises into the engine.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

HEALTH_FILE = "dispatch-health.json"
DISPATCHER_SILENT_SECONDS = 300
_COUNT_CACHE_SECONDS = 30
_NOTICE_SESSIONS_MAX = 256

_count_cache: dict[str, Any] = {"at": 0.0, "home": None, "counts": None}
_notice_seen: dict[str, str] = {}


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def health_path(home: Optional[Path] = None) -> Path:
    return (home or hermes_home()) / "logs" / HEALTH_FILE


def read_health(home: Optional[Path] = None) -> Optional[dict]:
    try:
        data = json.loads(health_path(home).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_health(data: dict, home: Optional[Path] = None) -> None:
    path = health_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, path)


def _tick_holds(result: Any) -> dict[str, str]:
    """Per-card hold reason from one DispatchResult (duck-typed, fields optional)."""
    holds: dict[str, str] = {}
    for entry in getattr(result, "skipped_per_profile_capped", None) or []:
        task_id, assignee, running = (list(entry) + [None, None, None])[:3]
        holds[str(task_id)] = f"per_profile_cap ({assignee} running {running})"
    for entry in getattr(result, "respawn_guarded", None) or []:
        task_id, reason = (list(entry) + [None, None])[:2]
        holds[str(task_id)] = f"respawn_guarded:{reason or 'unknown'}"
    for task_id in getattr(result, "skipped_nonspawnable", None) or []:
        holds[str(task_id)] = "nonspawnable_assignee"
    for task_id in getattr(result, "skipped_unassigned", None) or []:
        holds[str(task_id)] = "unassigned"
    for task_id in getattr(result, "rate_limited", None) or []:
        holds[str(task_id)] = "rate_limited"
    return holds


def record_dispatch_tick(*, outcome: str = "ok", result: Any = None, dry_run: bool = False,
                         board: Optional[str] = None, now: Optional[float] = None,
                         home: Optional[Path] = None, **_: Any) -> None:
    """``on_kanban_dispatch_tick`` observer for the default board. Never raises."""
    if dry_run or board not in (None, "", "default"):
        return
    try:
        now = time.time() if now is None else now
        data = read_health(home) or {}
        data["last_tick_at"] = now
        data["last_outcome"] = outcome
        if outcome == "skipped_locked":
            # Another dispatcher holds the board lock and is making progress; keep holds.
            _write_health(data, home)
            return
        spawned = list(getattr(result, "spawned", None) or [])
        if spawned:
            data["last_spawn_at"] = now
            data["last_spawn_count"] = len(spawned)
        data["memory_pressure"] = getattr(result, "memory_pressure", None)
        previous = data.get("holds") or {}
        holds = {}
        for task_id, reason in _tick_holds(result).items():
            prior = previous.get(task_id) or {}
            since = prior.get("since") if prior.get("reason") == reason else None
            holds[task_id] = {"reason": reason, "since": since or now}
        data["holds"] = holds
        _write_health(data, home)
    except Exception:
        pass


def estop_state(home: Optional[Path] = None) -> Optional[dict]:
    """``$HERMES_HOME/ESTOP`` sentinel: None when absent; a corrupt file still counts."""
    path = (home or hermes_home()) / "ESTOP"
    try:
        if not path.exists():
            return None
    except OSError:
        return {"reason": None, "engaged_at": None}
    state: dict[str, Any] = {"reason": None, "engaged_at": None}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            state = {"reason": raw.get("reason") or None, "engaged_at": raw.get("engaged_at") or None}
    except (OSError, ValueError, TypeError):
        pass
    return state


def board_counts(home: Optional[Path] = None, *, use_cache: bool = True) -> Optional[dict[str, int]]:
    """Task counts by status from kanban.db (read-only); None when unreadable."""
    home = home or hermes_home()
    now = time.time()
    if (use_cache and _count_cache["home"] == str(home)
            and now - _count_cache["at"] < _COUNT_CACHE_SECONDS):
        return _count_cache["counts"]
    db = home / "kanban.db"
    counts: Optional[dict[str, int]] = None
    if db.exists():
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            try:
                counts = {row[0]: int(row[1]) for row in conn.execute(
                    "SELECT status, COUNT(*) FROM tasks GROUP BY status")}
            finally:
                conn.close()
        except sqlite3.Error:
            counts = None
    _count_cache.update(at=now, home=str(home), counts=counts)
    return counts


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _engaged_age(engaged_at: Optional[str], now: float) -> Optional[str]:
    if not engaged_at:
        return None
    try:
        from datetime import datetime
        return _age(now - datetime.fromisoformat(str(engaged_at)).timestamp())
    except (TypeError, ValueError):
        return None


def stall_condition(home: Optional[Path] = None, now: Optional[float] = None) -> Optional[tuple[str, str]]:
    """``(key, message)`` for a board-wide liveness stall, else None."""
    home = home or hermes_home()
    now = time.time() if now is None else now
    counts = board_counts(home) or {}
    waiting = counts.get("ready", 0) + counts.get("review", 0)
    paused = estop_state(home)
    if paused is not None:
        if not waiting:
            return None
        age = _engaged_age(paused.get("engaged_at"), now)
        since = f" for {age}" if age else ""
        reason = f" (reason: {paused['reason']})" if paused.get("reason") else " (no reason recorded)"
        return (
            f"estop:{paused.get('engaged_at')}:{waiting}",
            f"Hermes emergency stop (ESTOP) has been engaged{since}{reason}. The Kanban "
            f"dispatcher and every cron job — including the delivery stall supervisor — are "
            f"paused, so {waiting} ready/review card(s) cannot start. Tell the operator and ask "
            f"whether to run `hermes resume`. Do not work around it with `hermes kanban "
            f"dispatch`: the manual CLI pass bypasses the operator's stop.",
        )
    health = read_health(home)
    if waiting and health and health.get("last_tick_at"):
        silent = now - float(health["last_tick_at"])
        if silent >= DISPATCHER_SILENT_SECONDS:
            return (
                f"silent:{int(float(health['last_tick_at']))}",
                f"The Kanban gateway dispatcher has not ticked for {_age(silent)} while "
                f"{waiting} ready/review card(s) wait. Check `hermes gateway status` and "
                f"`kanban.dispatch_in_gateway`; tell the operator before dispatching by hand.",
            )
    return None


def liveness_notice(*, session_id: Optional[str] = None, is_first_turn: bool = False,
                    home: Optional[Path] = None, now: Optional[float] = None, **_: Any) -> Optional[dict]:
    """``pre_llm_call`` hook: inject a stall notice once per session per condition."""
    try:
        condition = stall_condition(home, now)
        key = session_id or ""
        if condition is None:
            _notice_seen.pop(key, None)
            return None
        cond_key, message = condition
        if not is_first_turn and _notice_seen.get(key) == cond_key:
            return None
        if len(_notice_seen) >= _NOTICE_SESSIONS_MAX:
            _notice_seen.clear()
        _notice_seen[key] = cond_key
        return {"context": f"[software-delivery liveness] {message}"}
    except Exception:
        return None


def _supervisor_last_run(home: Path) -> Optional[str]:
    try:
        data = json.loads((home / "cron" / "jobs.json").read_text())
    except (OSError, ValueError):
        return None
    jobs = data if isinstance(data, list) else data.get("jobs", [])
    for job in jobs:
        if job.get("name") == "Kanban stall supervisor":
            return job.get("last_run_at") or "never"
    return None


def liveness_report(home: Optional[Path] = None, now: Optional[float] = None) -> list[str]:
    """Doctor lines: ESTOP, dispatcher telemetry, supervisor freshness, board counts."""
    home = home or hermes_home()
    now = time.time() if now is None else now
    lines = []
    paused = estop_state(home)
    if paused is None:
        lines.append("estop: not engaged")
    else:
        age = _engaged_age(paused.get("engaged_at"), now)
        lines.append(
            f"estop: ENGAGED{f' for {age}' if age else ''} (reason: {paused.get('reason') or 'none'}) "
            f"— dispatcher and cron paused; `hermes resume` to lift")
    health = read_health(home)
    if not health or not health.get("last_tick_at"):
        lines.append("dispatcher: no tick telemetry yet (gateway not restarted since install, or not running)")
    else:
        spawn = health.get("last_spawn_at")
        lines.append(
            f"dispatcher: last tick {_age(now - float(health['last_tick_at']))} ago "
            f"({health.get('last_outcome')}), last spawn "
            f"{(_age(now - float(spawn)) + ' ago') if spawn else 'never recorded'}")
        holds = health.get("holds") or {}
        if holds:
            by_reason: dict[str, int] = {}
            for hold in holds.values():
                label = str(hold.get("reason", "")).split(" (")[0]
                by_reason[label] = by_reason.get(label, 0) + 1
            lines.append("dispatcher holds: " + ", ".join(f"{r}={n}" for r, n in sorted(by_reason.items())))
        if health.get("memory_pressure"):
            lines.append(f"dispatcher memory pressure: {health['memory_pressure']}")
    last_run = _supervisor_last_run(home)
    if last_run is not None:
        lines.append(f"stall supervisor last run: {last_run}")
    counts = board_counts(home, use_cache=False)
    if counts is not None:
        keys = ("ready", "review", "running", "todo", "blocked", "triage")
        lines.append("board: " + ", ".join(f"{k}={counts.get(k, 0)}" for k in keys))
    return lines
