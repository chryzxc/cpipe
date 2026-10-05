"""One conversation per delivery in the Hermes conversation list, showing the pipeline as it runs.

Deterministic, no LLM: on each dispatch tick, new board events of delivery cards are rendered as
messages into a `source=cpipe` session titled after the delivery. A delivery is its worktree: every
card delivery_submit creates for one request (map, plan, build, verify, fix rounds) shares it.
Nothing is injected into the coordinator's chat, so the flow costs no tokens.
"""

from __future__ import annotations

import fcntl
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional
from .home import root as _root_home

SOURCE = "cpipe"
KINDS = ("created", "claimed", "review_requested", "changes_requested", "completed", "blocked",
         "unblocked", "timed_out", "crashed", "gave_up", "rate_limited", "block_loop_detected")
BACKFILL_SECONDS = 12 * 3600  # first run: show deliveries active in the last 12h, not the whole board
STAGE_RE = re.compile(r"^(map|plan|verify|fix \d+|design [a-z])\s*:\s*", re.I)
URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
ICON = {"Map": "🗺", "Plan": "📐", "Build": "🔨", "Review": "🔍", "Verify": "🧪"}


def _short(text: Any, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _stage(title: str) -> str:
    m = STAGE_RE.match(title or "")
    return m.group(1).title() if m else "Build"


def _base_title(title: str) -> str:
    while STAGE_RE.match(title or ""):
        title = STAGE_RE.sub("", title, count=1)
    return title


def _dur(start: Any, end: Any) -> str:
    if not start or not end:
        return ""
    s = int(end - start)
    return f" · {s // 60}m {s % 60:02d}s" if s >= 60 else f" · {s}s"


def _quote(text: Any, n: int = 500) -> str:
    text = _short(text, n)
    return f"\n> {text}" if text else ""


def _session_id(worktree: str) -> str:
    return "cpipe_flow_" + re.sub(r"[^\w-]", "_", Path(worktree).name)


def _header(conn: sqlite3.Connection, worktree: str, title: str) -> str:
    cards = conn.execute("SELECT id, title, assignee FROM tasks WHERE workspace_path = ? AND created_by = "
                         "'delivery_submit' ORDER BY created_at, id", (worktree,)).fetchall()
    repo = Path(worktree).parent.parent.name if Path(worktree).parent.name == ".worktrees" else ""
    route = " → ".join(f"{_stage(t)} @{a}" for _, t, a in cards)
    return (f"## ⛓ Pipeline started\n**{title}**\n\n" + (f"Repo: `{repo}` · " if repo else "")
            + f"Worktree: `{Path(worktree).name}`\n\nRoute: {route} → Review → you")


def render(conn: sqlite3.Connection, ev: dict, first: bool) -> Optional[str]:
    """Markdown for one board event, or None when it adds nothing to the flow."""
    kind, stage, who, p = ev["kind"], _stage(ev["title"]), ev["profile"] or ev["assignee"] or "?", ev["payload"]
    card = f"`{ev['task_id']}`"
    if kind == "completed" and (stage == "Build" or stage.startswith("Fix")) and conn.execute("SELECT 1 FROM task_events WHERE task_id = ? AND kind = 'review_requested' "
                                 "AND id < ?", (ev["task_id"], ev["id"])).fetchone():
        stage = "Review"
    label = f"{ICON.get(stage, '🔁')} **{stage}**"
    summary = p.get("summary") or ev["run_summary"]
    if kind == "created":
        if first:
            return _header(conn, ev["workspace"], _base_title(ev["title"]))
        if stage.startswith("Fix"):
            body = conn.execute("SELECT body FROM tasks WHERE id = ?", (ev["task_id"],)).fetchone()
            return f"## 🔁 {stage} opened · @{who} {card}" + _quote(body and body[0], 400)
        return None  # the other stages of a new delivery are listed in its header
    if kind == "claimed":
        return f"▶ {label} started · @{who} {card}"
    if kind == "review_requested":
        return f"{label} handed to review · @{who} → review {card}{_dur(ev['run_start'], ev['ts'])}" + _quote(summary)
    if kind == "changes_requested":
        return f"↩ **Review** requested changes · @{who} → back to the implementer {card}" + _quote(p.get("reason"), 700)
    if kind == "completed":
        pr = URL_RE.search(str(summary or ""))
        nxt = conn.execute("SELECT t.title, t.assignee FROM task_links l JOIN tasks t ON t.id = l.child_id "
                           "WHERE l.parent_id = ? UNION SELECT title, assignee FROM tasks WHERE instr(body, ?) > 0",
                           (ev["task_id"], f"BUILD CARD: {ev['task_id']}")).fetchall()  # + a verify run beside review
        lines = [f"✅ {label} {'approved' if stage == 'Review' else 'done'} · @{who} {card}"
                 f"{_dur(ev['run_start'], ev['ts'])}" + _quote(summary)]
        if pr:
            lines.append(f"PR: {pr.group(0)}")
        lines += [f"→ Next: **{_stage(t)}** · @{a}" for t, a in nxt]
        if not nxt and stage in ("Review", "Verify"):
            lines.append("🏁 **Ready for you to test**")
        return "\n".join(lines)
    if kind == "blocked":
        ask = ("\nThe fix-round limit is reached, so it's your call: one more fix round, or stop and finish "
               "the PR yourself." if re.search(r"(?i)round limit", str(p.get("reason") or "")) else "\nNeeds a decision.")
        return f"⏸ {label} blocked · @{who} {card}" + _quote(p.get("reason"), 700) + ask
    if kind == "unblocked":
        return f"▶ {label} unblocked {card}"
    if kind == "timed_out":
        return f"⏱ {label} timed out · @{who} {card} after {int(p.get('elapsed_seconds') or 0) // 60}m; retrying"
    if kind == "rate_limited":
        return f"⏳ {label} rate-limited · @{who} {card}; retrying after the cooldown"
    if kind == "crashed":
        return f"💥 {label} worker crashed · @{who} {card}; retrying"
    if kind == "gave_up":
        return f"✖ {label} gave up · {card}" + _quote(p.get("error"), 300)
    return f"🛑 {label} sent to triage (block loop) {card}" + _quote(p.get("reason"), 300)


def pending(conn: sqlite3.Connection, cursor: int, limit: int = 200) -> list[dict]:
    marks = ",".join("?" * len(KINDS))
    rows = conn.execute(
        f"SELECT e.id, e.task_id, e.kind, e.payload, e.created_at, t.title, t.assignee, t.workspace_path, "
        f"r.profile, r.started_at, r.summary FROM task_events e JOIN tasks t ON t.id = e.task_id "
        f"LEFT JOIN task_runs r ON r.id = e.run_id WHERE e.id > ? AND e.kind IN ({marks}) "
        f"AND t.created_by = 'delivery_submit' AND t.workspace_path LIKE '%/.worktrees/%' "
        f"ORDER BY e.id LIMIT ?", (cursor, *KINDS, limit)).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r[3] or "{}")
        except ValueError:
            payload = {}
        out.append({"id": r[0], "task_id": r[1], "kind": r[2], "payload": payload if isinstance(payload, dict) else {},
                    "ts": r[4], "title": r[5] or r[1], "assignee": r[6], "workspace": r[7], "profile": r[8],
                    "run_start": r[9], "run_summary": r[10]})
    return out


def sync(conn: sqlite3.Connection, db, state: dict, now: Optional[float] = None) -> dict:
    """Append new events to their delivery conversations. Returns the updated state."""
    if "cursor" not in state:
        since = (now or time.time()) - BACKFILL_SECONDS
        row = conn.execute("SELECT max(id) FROM task_events WHERE created_at < ?", (since,)).fetchone()
        state["cursor"] = row[0] or 0
    flows = state.setdefault("flows", {})
    for ev in pending(conn, state["cursor"]):
        state["cursor"] = ev["id"]
        sid, first = _session_id(ev["workspace"]), ev["workspace"] not in flows
        text = render(conn, ev, first)
        if text is None:
            continue
        if first:
            db.create_session(sid, SOURCE)
            title = f"⛓ {_short(_base_title(ev['title']), 70)}"
            try:
                db.set_session_title(sid, title)
            except ValueError:  # titles are unique: a resubmit or port of the same request
                db.set_session_title(sid, f"{title} · {Path(ev['workspace']).name}")
            flows[ev["workspace"]] = sid
            if ev["kind"] != "created":  # joined mid-delivery (backfill window): header first
                db.append_message(sid, "assistant", _header(conn, ev["workspace"], _base_title(ev["title"])),
                                  timestamp=ev["ts"])
        db.append_message(sid, "assistant", text, timestamp=ev["ts"])
    return state


def sync_flows(*, dry_run: bool = False, board: Optional[str] = None, home: Optional[Path] = None,
               **_: Any) -> None:
    """``on_kanban_dispatch_tick`` observer (gateway process). Never raises."""
    if dry_run or board not in (None, "", "default"):
        return
    try:
        home = home or _root_home()
        state_path = home / "state" / "cpipe-flow.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path.with_suffix(".lock"), "w") as lock:
            try:  # a CLI `hermes kanban dispatch` ticks too: one syncer at a time, never a duplicate message
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return
            try:
                state = json.loads(state_path.read_text())
            except (OSError, ValueError):
                state = {}
            before = json.dumps(state)
            from hermes_state import SessionDB
            from hermes_cli.kanban_db_connect import connect_closing
            with connect_closing() as conn:
                state = sync(conn, SessionDB(db_path=home / "state.db"), state)
            if json.dumps(state) != before:
                state_path.write_text(json.dumps(state))
    except Exception:
        pass
