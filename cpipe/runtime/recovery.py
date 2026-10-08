"""Bounded infrastructure recovery using canonical Hermes events and comments.

Only a known round startup import failure or an explicit runtime pause receipt
is eligible. Product decisions, credentials, scope and failed gates are not retries.
"""
from __future__ import annotations

import json
import re
import time

from . import hermes_api as h

MAX_RECOVERIES = 2
AUTHOR = "cpipe:runtime-recovery"
_MISSING = re.compile(r"(?:ModuleNotFoundError|ImportError): (?:No module named|cannot import name) ['\"]([^'\"]+)['\"]")


def signature(reason: str) -> str | None:
    match = _MISSING.search(reason or "")
    return f"dependency:{match.group(1)}" if match else None


def eligible(conn, task) -> str | None:
    if not task or task.created_by != "cpipe" or task.status not in ("blocked", "triage"):
        return None
    events = h.events_after(conn, task.id)
    blocks = [e for e in events if e.kind in ("blocked", "block_loop_detected")]
    block = blocks[-1] if blocks else None
    reason = str((block.payload or {}).get("reason", "")) if block else ""
    dependency = signature(reason)
    # Legacy runners incorrectly classified import failures as needs_input. Only
    # accept their actual startup/error envelope, not text in a product question.
    startup = ("cpipe.round startup:" in reason or
               ("cpipe.round" in reason and "exited with code" in reason and "Last output:" in reason))
    if dependency and startup:
        return dependency
    for comment in reversed(h.comments_after(conn, task.id)):
        if comment.author != "cpipe:runtime-block":
            continue
        try:
            result = json.loads(comment.body)
        except (TypeError, ValueError):
            return None
        if (block and (block.payload or {}).get("kind") == "transient" and
                result.get("run_id") == block.run_id and
                result.get("runtime_failure") == "transient" and result.get("node") == "paused"):
            return "transient:paused"
        return None
    return None


def _attempts(conn, task_id: str, now: float):
    attempts = [c for c in h.comments_after(conn, task_id) if c.author == AUTHOR]
    if len(attempts) >= MAX_RECOVERIES:
        return None
    if attempts and now < attempts[-1].created_at + 30 * len(attempts):
        return None
    return attempts


def recover(conn, task, *, log, now: float | None = None) -> bool:
    """Two attempts total, including failed probes, with native transaction CAS."""
    category = eligible(conn, task)
    now = time.time() if now is None else now
    if not category or _attempts(conn, task.id, now) is None:
        return False
    from .launcher import preflight
    error = None
    try:
        preflight()
    except RuntimeError as exc:
        error = str(exc)
    # Probing does not hold the board write lock. Atomically reserve an attempt;
    # native transitions run after commit because they fire post-commit hooks.
    with h.kb.write_txn(conn):
        current = h.get_task(conn, task.id)
        attempts = _attempts(conn, task.id, now)
        if (attempts is None or not current or current.current_run_id != task.current_run_id
                or eligible(conn, current) != category):
            return False
        h.comment(conn, task.id, json.dumps({"attempt": len(attempts) + 1, "signature": category,
                                            "ready": error is None}), author=AUTHOR)
    if error:
        log(error)
        return False
    resumed = h.resume(conn, task.id, runtime_ready=True)
    log(f"runtime recovery {task.id}: status={h.get_task(conn, task.id).status}; resumed={resumed}")
    return resumed
