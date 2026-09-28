"""Write why a card stopped onto the card itself. Deterministic, no LLM.

The kanban card view shows comments but not the block reason, and a crash only says "pid gone".
On each dispatch tick, every blocked/triage card whose latest block has not been explained yet
gets one comment: the recorded reason, the last failure error, and the bot's own startup/runtime
errors logged during that failed run (auth fallbacks, unknown providers, tracebacks).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

AUTHOR = "why-blocked"
REASON_KINDS = ("blocked", "gave_up", "block_loop_detected")
LOG_SIGNAL = re.compile(r"auth failed|Unknown provider|Traceback|Error:|Killed|SIGTERM|rate.?limit|quota", re.I)
LOG_NOISE = re.compile(r"MCP server|Tool terminal returned error", re.I)


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _short(text: Any, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def run_log_errors(profile: str, start: float, end: float, home: Path, limit: int = 3) -> list[str]:
    """Signal lines from the profile's errors.log written while the run was alive."""
    path = home / "profiles" / profile / "logs" / "errors.log"
    found: list[str] = []
    try:
        lines = path.read_text(errors="replace").splitlines()[-2000:]
    except OSError:
        return found
    for line in lines:
        try:
            at = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        if start - 5 <= at <= end + 5 and LOG_SIGNAL.search(line) and not LOG_NOISE.search(line):
            msg = _short(line[24:], 220)
            if msg not in found:
                found.append(msg)
    return found[-limit:]


def explain(conn: sqlite3.Connection, card: dict, home: Path) -> str:
    try:
        payload = json.loads(card["payload"] or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    reason = payload.get("reason") or payload.get("error") or "no reason was recorded"
    lines = [f"Why blocked ({card['kind']}{', ' + card['block_kind'] if card['block_kind'] else ''}): {_short(reason, 600)}"]
    run = conn.execute("SELECT profile, started_at, ended_at, outcome, error FROM task_runs WHERE task_id=? "
                       "ORDER BY id DESC LIMIT 1", (card["id"],)).fetchone()
    if run and run[4] and _short(run[4], 80) not in lines[0]:
        lines.append(f"Last run ({run[3]}): {_short(run[4], 300)}")
    if run and run[0] and run[1]:
        errors = run_log_errors(run[0], run[1], run[2] or time.time(), home)
        if errors:
            lines.append(f"{run[0]} logged during that run:\n" + "\n".join(f"- {e}" for e in errors))
    lines.append("Next: open Mission Control, or reply `continue <id> <what to do>` / `archive <id>` / `resubmit <id>`.")
    return "\n".join(lines)


def pending(conn: sqlite3.Connection, explained: dict) -> list[dict]:
    """Blocked/triage cards whose latest block event has no explanation comment yet."""
    marks = ",".join("?" * len(REASON_KINDS))
    rows = conn.execute(
        f"SELECT t.id, t.block_kind, e.id, e.kind, e.payload FROM tasks t JOIN task_events e ON e.id = "
        f"(SELECT max(id) FROM task_events WHERE task_id = t.id AND kind IN ({marks})) "
        f"WHERE t.status IN ('blocked','triage')", REASON_KINDS).fetchall()
    return [{"id": r[0], "block_kind": r[1], "event_id": r[2], "kind": r[3], "payload": r[4]}
            for r in rows if explained.get(r[0], 0) < r[2]]


def explain_blocks(*, dry_run: bool = False, board: Optional[str] = None, home: Optional[Path] = None,
                   **_: Any) -> None:
    """``on_kanban_dispatch_tick`` observer (gateway process). Never raises."""
    if dry_run or board not in (None, "", "default"):
        return
    try:
        home = home or _home()
        state_path = home / "state" / "why-blocked.json"
        try:
            explained = json.loads(state_path.read_text())
        except (OSError, json.JSONDecodeError):
            explained = {}
        from hermes_cli import kanban_db
        from hermes_cli.kanban_db_connect import connect_closing
        with connect_closing() as conn:
            todo = pending(conn, explained)
            for card in todo[:10]:  # bounded per tick; the rest follow on the next ticks
                kanban_db.add_comment(conn, card["id"], AUTHOR, explain(conn, card, home))
                explained[card["id"]] = card["event_id"]
        if todo:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(explained))
    except Exception:
        pass
