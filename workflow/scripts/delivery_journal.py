"""Delivery journal: one JSON line per decision the plugin makes. Best-effort, never raises.

Every classification change, repair, escalation and waste signal lands in
``$HERMES_HOME/logs/delivery-journal.jsonl`` so an incident is a `hermes software-delivery log`
away instead of hand-written SQL, and the targets in docs/plans are measured from it.

Kinds (stable names):
  monitor.state            a card's verdict changed (state, cause)
  monitor.repair           a repair ran (repair, ok, output)
  monitor.would_repair     observe mode: the repair that would have run
  monitor.escalate         escalation sent (how: block|digest)
  hold.start / hold.end    an engine hold began or ended for a card
  triage.retry             board triage retried a card
  card.outcome             a card reached done/blocked/gave_up (runs, seconds)
  waste.fast_fail          3 runs in a row under 60 s
  waste.identical_failure  the same error signature after a retry
  waste.duplicate_card     delivery_submit refused a duplicate
  waste.quota_respawn      a respawn into a provider quota wall
  nudge                    operator message that only asks for progress
  claim.unsupported        a progress claim the board does not back
  approval.requested       a worker session hit an approval prompt
  session.end              session metrics (tokens, duration)
  baseline                 delivery_baseline.py numbers
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterator, Optional

MAX_BYTES = 20 * 1024 * 1024


def journal_path(home: Optional[Path] = None) -> Path:
    home = home or Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    return home / "logs" / "delivery-journal.jsonl"


def record(kind: str, card: Optional[str] = None, *, home: Optional[Path] = None, **detail) -> None:
    try:
        path = journal_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
        line = {"ts": round(detail.pop("ts", None) or time.time(), 3), "kind": kind, "card": card,
                "actor": detail.pop("actor", None) or os.environ.get("HERMES_PROFILE") or "plugin", **detail}
        with path.open("a") as fh:
            fh.write(json.dumps(line, default=str) + "\n")
    except Exception:
        pass


def read(since: Optional[float] = None, card: Optional[str] = None, kind_prefix: Optional[str] = None,
         *, home: Optional[Path] = None) -> Iterator[dict]:
    path = journal_path(home)
    for p in (path.with_name(path.name + ".1"), path):
        try:
            fh = p.open()
        except OSError:
            continue
        with fh:
            for raw in fh:
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if since and entry.get("ts", 0) < since:
                    continue
                if card and card not in (entry.get("card"), entry.get("chain")):
                    continue
                if kind_prefix and not str(entry.get("kind", "")).startswith(kind_prefix):
                    continue
                yield entry


def parse_since(text: Optional[str], now: Optional[float] = None) -> Optional[float]:
    """'90m' / '2h' / '3d' -> epoch seconds; None when empty."""
    if not text:
        return None
    unit = {"m": 60, "h": 3600, "d": 86400}.get(text[-1])
    if unit is None:
        raise ValueError(f"--since takes <n>m|h|d, got {text!r}")
    return (now or time.time()) - float(text[:-1]) * unit


def format_line(entry: dict) -> str:
    ts = time.strftime("%m-%d %H:%M", time.localtime(entry.get("ts", 0)))
    rest = {k: v for k, v in entry.items() if k not in ("ts", "kind", "card", "actor")}
    detail = " ".join(f"{k}={v}" for k, v in rest.items() if v not in (None, "", [], {}))
    return f"{ts}  {entry.get('kind', ''):<22} {entry.get('card') or '-':<12} {detail}"[:400]
