#!/usr/bin/env python3
"""Delivery monitor — the plugin's own view of whether work is moving. Deterministic, no LLM.

Hermes' own signals can be wrong (stale respawn guards, dead claims, a coordinator saying "will
resume"). This script trusts only the board (``kanban.db``) and ``gh``, gives every open card one
verdict, and acts on it:

  PROGRESSING            a live worker, or a card inside its claim grace window
  WAITING(<named>)       a cap, a quota reset, a registered PR/CI watch, a parent, or a person
                         who has already been told
  STUCK(<cause>)         anything else: repaired when a known repair exists, else escalated

Escalation reaches the chat that started the work: a ready/running card is blocked with
``--kind needs_input "<CAUSE>: …; next: …"`` (the ``blocked`` event wakes every subscribed chat);
any other card gets a one-line notice injected into that chat's next turn by the plugin, plus the
digest printed here (cron delivers stdout to the operator's bot channel; empty stdout sends nothing).

Mode: ``observe`` (default) journals ``monitor.would_repair`` and never changes the board;
``act`` runs repairs and blocks. Set ``monitor_mode: act`` in ``$HERMES_HOME/delivery/config.yaml``
or ``HERMES_DELIVERY_MONITOR_MODE``. ESTOP always forces observe. Every decision is journaled
(see delivery_journal.py). Runs as a cron job every 5 minutes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import delivery_journal as journal  # noqa: E402

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DB = HERMES_HOME / "kanban.db"
STATE = HERMES_HOME / "state" / "delivery-state.json"
WATCHES = HERMES_HOME / "state" / "delivery-watches.json"
NOTICES = HERMES_HOME / "state" / "delivery-notices.json"
LEGACY_STATE = HERMES_HOME / "state" / "stall-alert.json"

CLAIM_GRACE_MINUTES = 10      # ready/review card nobody has claimed yet: normal
ORPHAN_REVIEW_MINUTES = 15    # review card with no reviewer, no hold
UNKNOWN_STALL_MINUTES = 20    # anything else not moving
DEAD_WORKER_MINUTES = 10      # running, PID gone, no heartbeat
AT_CAP_ESCALATE_MINUTES = 60
HOLD_FRESH_MINUTES = 10       # a respawn_guarded event newer than this is a live hold
QUOTA_WINDOW_MINUTES = 30
QUOTA_EVENTS = 3
QUOTA_PAUSE_MINUTES = 30      # provider reset time is not on the board; retry after this
RUN_BUDGET = 10
FAST_FAIL_SECONDS = 60
FAST_FAIL_RUNS = 3
REPAIR_ATTEMPTS = 2           # a repair that did not stick this many times escalates instead
EXPIRE_NOTICE_DAYS = 7
EXPIRE_ARCHIVE_DAYS = 10
EXPIRE_NOTICE_LEAD_DAYS = 3   # never archive a card whose chat was not warned at least this long ago
DISPATCHER_SILENT_MINUTES = 10
NEEDS_HUMAN_REMIND_HOURS = 24
ENGINE_WINDOW_MINUTES = 30
ENGINE_EVENT_THRESHOLD = 3
REMIND_HOURS = 12
LIST_CAP = 12
MESSAGE_BUDGET = 1600         # stay under chat limits (Discord: 2000 chars)

OPEN = ("todo", "ready", "review", "running", "blocked", "triage", "scheduled")
# Events that mean the card itself moved; comments and guard noise do not count.
QUIET_KINDS = ("heartbeat", "commented", "respawn_guarded", "claim_extended")
FAILURE_OUTCOMES = ("crashed", "timed_out", "gave_up", "spawn_failed", "rate_limited")
FAILURE_EVENTS = ("blocked", "gave_up", "crashed", "timed_out", "protocol_violation")
# The respawn guard's quota text left behind by a requeue that a later, healthy run superseded.
STALE_GUARD_RE = re.compile(r"rate.?limit|quota wall|requeued without counting", re.I)
DESYNC_RE = re.compile(r"unknown id or not running", re.I)

WHY = {
    "needs_input": "waiting on YOUR decision",
    "capability": "missing tool/env/permission, needs a fix",
    "transient": "waiting on quota/retry, should resume by itself",
}
NEXT = {
    "DEAD_WORKER": "the claim is released so the card respawns; restart the gateway if it recurs",
    "STALE_GUARD": "a stale quota error holds the card; clear it (`hermes kanban show` → last error)",
    "AUTH_BLOCKED": "the assignee's provider login failed; fix credentials, then `hermes kanban unblock`",
    "ORPHAN_REVIEW": "no reviewer picked it up; check the reviewer profile, then unblock",
    "UNKNOWN_STALL": "nothing explains the stall; inspect `hermes kanban show` and unblock or archive",
    "AT_CAP": "the assignee has been at its concurrency cap for an hour; check its running cards",
    "RUN_BUDGET": f"over {RUN_BUDGET} runs; narrow the card or give the worker what it lacks",
    "FAST_FAIL": "workers die within a minute; fix the environment before retrying",
    "IDENTICAL_FAILURE": "the retry failed the same way; change something before retrying",
    "LIFECYCLE_DESYNC": "the worker lost its claim mid-run; restart the gateway / update Hermes, then unblock",
    "TRIAGE_PARKED": "the loop breaker parked it; decide: retry with new input, narrow, or archive",
    "UNSUBSCRIBED": "no chat follows this card; decide it here",
    "EXPIRING": f"undecided for {EXPIRE_NOTICE_DAYS}+ days; decide, or it is archived (with a final comment) "
                f"{EXPIRE_NOTICE_LEAD_DAYS} days after this notice",
    "WATCH_FAILED": "the watched PR/CI failed; fix it or unblock with new instructions",
    "WATCH_EXPIRED": "the watched PR/CI did not finish in time; check it",
}


@dataclass
class Verdict:
    card: str
    state: str                    # PROGRESSING | WAITING | STUCK
    cause: str
    evidence: str = ""
    repair: Optional[str] = None  # reclaim | clear_guard | quota_pause | resume | archive
    escalate: bool = False
    waiting_on: str = ""          # WAITING(<named thing>)
    chain: str = ""
    signature: str = ""
    blocked_children: list = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.state == "WAITING":
            return f"WAITING({self.waiting_on or self.cause})"
        return f"{self.state}({self.cause})" if self.state == "STUCK" else self.state


# ---------------------------------------------------------------- helpers

def signature(text) -> str:
    """Stable id for an error: numbers, paths, ids, hex and timestamps stripped, then hashed."""
    t = str(text or "").lower()
    t = re.sub(r"(?:/[\w.\-@]+)+/?", "<path>", t)
    t = re.sub(r"\bt_[0-9a-f]+\b|\b[0-9a-f]{7,}\b", "<id>", t)
    t = re.sub(r"\d+(?:\.\d+)?", "<n>", t)
    t = " ".join(t.split())
    return hashlib.sha1(t.encode()).hexdigest()[:10] if t else ""


def _short(text, n=160):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _age(seconds):
    m = int(max(0, seconds) // 60)
    return f"{m}m" if m < 120 else f"{m // 60}h" if m < 2880 else f"{m // 1440}d"


def _payload(raw) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, default=str))
    os.replace(tmp, path)


def monitor_mode(home: Path = HERMES_HOME) -> str:
    mode = os.environ.get("HERMES_DELIVERY_MONITOR_MODE", "")
    if not mode:
        try:
            m = re.search(r"^monitor_mode:\s*(\w+)", (home / "delivery" / "config.yaml").read_text(), re.M)
            mode = m.group(1) if m else ""
        except OSError:
            pass
    return "act" if mode == "act" else "observe"


def hermes(*args: str) -> tuple[bool, str]:
    exe = os.environ.get("HERMES_BIN") or shutil.which("hermes") or str(Path.home() / ".local/bin/hermes")
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return out.returncode == 0, _short(out.stdout or out.stderr, 300)


# ---------------------------------------------------------------- board snapshot

def load_board(conn, now, home: Path = HERMES_HOME) -> dict:
    """One read-only snapshot of everything the classifier looks at."""
    conn.row_factory = sqlite3.Row
    q = ",".join("?" * len(OPEN))
    tasks = {r["id"]: dict(r) for r in conn.execute(f"SELECT * FROM tasks WHERE status IN ({q})", OPEN)}
    runs: dict = {}
    events: dict = {}
    for tid in tasks:
        runs[tid] = [dict(r) for r in conn.execute(
            "SELECT * FROM task_runs WHERE task_id=? ORDER BY id", (tid,))]
        events[tid] = [dict(r) for r in conn.execute(
            "SELECT id, kind, payload, created_at FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 50",
            (tid,))][::-1]
    parents: dict = {}
    children: dict = {}
    for p, c in conn.execute("SELECT parent_id, child_id FROM task_links"):
        parents.setdefault(c, []).append(p)
        children.setdefault(p, []).append(c)
    status_of = {r[0]: r[1] for r in conn.execute("SELECT id, status FROM tasks")}
    subs: dict = {}
    try:
        for r in conn.execute("SELECT * FROM kanban_notify_subs"):
            subs.setdefault(r["task_id"], []).append(dict(r))
    except sqlite3.OperationalError:
        pass
    since = now - QUOTA_WINDOW_MINUTES * 60
    rate_limited: dict = {}
    for (assignee,) in conn.execute(
            "SELECT t.assignee FROM task_events e JOIN tasks t ON t.id=e.task_id "
            "WHERE e.kind='rate_limited' AND e.created_at>=?", (since,)):
        rate_limited[assignee] = rate_limited.get(assignee, 0) + 1
    health = _load(home / "logs" / "dispatch-health.json")
    return {"tasks": tasks, "runs": runs, "events": events, "parents": parents, "children": children,
            "status_of": status_of, "subs": subs, "rate_limited": rate_limited,
            "holds": health.get("holds") or {}, "health": health}


def _moved_at(tid, b) -> float:
    moved = [e["created_at"] for e in b["events"][tid] if e["kind"] not in QUIET_KINDS]
    return max(moved) if moved else (b["tasks"][tid]["created_at"] or 0)


def _hold(tid, b, now) -> tuple[str, float]:
    """(reason, since) of the engine hold on a card: dispatch telemetry first, else guard events."""
    hold = b["holds"].get(tid)
    if hold:
        return str(hold.get("reason") or ""), float(hold.get("since") or now)
    guarded = [e for e in b["events"][tid] if e["kind"] == "respawn_guarded"]
    if guarded and now - guarded[-1]["created_at"] < HOLD_FRESH_MINUTES * 60 \
            and guarded[-1]["created_at"] >= _moved_at(tid, b):
        reason = _payload(guarded[-1]["payload"]).get("reason") or "unknown"
        first = guarded[-1]["created_at"]
        for e in reversed(guarded):
            if e["created_at"] < _moved_at(tid, b):
                break
            first = e["created_at"]
        return f"respawn_guarded:{reason}", first
    return "", now


def _failure_texts(tid, b) -> list[str]:
    """Newest-last failure evidence: run errors and failure events."""
    texts = []
    for r in b["runs"][tid]:
        if r.get("outcome") in FAILURE_OUTCOMES + ("blocked",) and (r.get("error") or r.get("summary")):
            texts.append((r.get("ended_at") or 0, r.get("error") or r.get("summary")))
    return [t for _, t in sorted(texts)]


def _block_reason(tid, b) -> str:
    for e in reversed(b["events"][tid]):
        if e["kind"] in ("blocked", "gave_up", "block_loop_detected"):
            p = _payload(e["payload"])
            return p.get("reason") or p.get("error") or ""
    return ""


def _chain_root(tid, b) -> str:
    seen, cur = set(), tid
    while b["parents"].get(cur) and cur not in seen:
        seen.add(cur)
        cur = sorted(b["parents"][cur])[0]
    return cur


def _subscribed(tid, b) -> bool:
    return bool(b["subs"].get(tid))


# ---------------------------------------------------------------- classification

def _waste(tid, b) -> Optional[Verdict]:
    """Retry loops, whatever the status: budget, fast-fail streak, identical failure, lost claim."""
    t, runs = b["tasks"][tid], b["runs"][tid]
    ended = [r for r in runs if r.get("ended_at")]
    texts = _failure_texts(tid, b)
    if texts and DESYNC_RE.search(texts[-1]):
        return Verdict(tid, "STUCK", "LIFECYCLE_DESYNC", _short(texts[-1]), escalate=True,
                       signature=signature(texts[-1]))
    if len(runs) > RUN_BUDGET:
        return Verdict(tid, "STUCK", "RUN_BUDGET", f"{len(runs)} runs", escalate=True)
    tail = ended[-FAST_FAIL_RUNS:]
    if len(tail) == FAST_FAIL_RUNS and all(
            r.get("outcome") in FAILURE_OUTCOMES + ("reclaimed",) and r.get("outcome") != "rate_limited"
            and (r["ended_at"] - (r.get("started_at") or r["ended_at"])) < FAST_FAIL_SECONDS for r in tail):
        return Verdict(tid, "STUCK", "FAST_FAIL", f"last {FAST_FAIL_RUNS} runs each ended in under "
                       f"{FAST_FAIL_SECONDS}s: " + _short(texts[-1] if texts else tail[-1].get("outcome"), 120),
                       escalate=True, signature=signature(texts[-1] if texts else ""))
    if t["status"] != "running" and len(texts) >= 2 and signature(texts[-1]) == signature(texts[-2]):
        retried = any(e["kind"] in ("unblocked", "promoted", "reclaimed") for e in b["events"][tid])
        if retried:
            return Verdict(tid, "STUCK", "IDENTICAL_FAILURE", _short(texts[-1]), escalate=True,
                           signature=signature(texts[-1]))
    return None


def classify(tid, b, now, st=None, watches=None) -> Verdict:
    """The one verdict for a card. Pure: reads the snapshot and prior monitor state only."""
    st = st or {}
    t = b["tasks"][tid]
    status = t["status"]
    idle = now - _moved_at(tid, b)
    chain = _chain_root(tid, b)

    def v(state, cause, evidence="", **kw):
        return Verdict(tid, state, cause, _short(evidence, 200), chain=chain, **kw)

    watch = (watches or {}).get(tid)
    if watch and status != "running":
        return v("WAITING", "WATCH", watch.get("ref", ""), waiting_on=f"{watch.get('kind')} {watch.get('ref')}")

    if status in ("blocked", "triage", "scheduled"):
        days = idle / 86400
        warned = st.get("expiry_warned", {}).get(tid)
        if status != "scheduled" and days >= EXPIRE_ARCHIVE_DAYS and warned \
                and now - warned >= EXPIRE_NOTICE_LEAD_DAYS * 86400:
            return v("STUCK", "EXPIRED", f"no decision for {int(days)}d", repair="archive")
        if status != "scheduled" and days >= EXPIRE_NOTICE_DAYS:
            return v("STUCK", "EXPIRING", f"no decision for {int(days)}d", escalate=True)
    if status != "running":
        waste = _waste(tid, b)
        if waste:
            waste.chain = chain
            return waste

    if status == "running":
        pid, beat = t.get("worker_pid"), t.get("last_heartbeat_at") or t.get("started_at") or t["created_at"]
        if pid and _pid_alive(pid):
            return v("PROGRESSING", "WORKER_ALIVE", f"pid {pid}, heartbeat {_age(now - (beat or now))} ago")
        if now - (beat or 0) < DEAD_WORKER_MINUTES * 60:
            return v("PROGRESSING", "CLAIM_PENDING", "worker starting")
        tries = st.get("repairs", {}).get(tid, {}).get("reclaim", 0)
        return v("STUCK", "DEAD_WORKER", f"pid {pid or 'none'} gone, no heartbeat for {_age(now - beat)}",
                 repair=None if tries >= 1 else "reclaim", escalate=tries >= 1)

    if status == "scheduled":
        resume_at = st.get("quota_pause", {}).get(tid)
        if resume_at:
            if now >= resume_at:
                return v("STUCK", "QUOTA_WALL", "quota pause elapsed", repair="resume")
            return v("WAITING", "QUOTA_WALL", waiting_on=f"quota reset ~{time.strftime('%H:%M', time.localtime(resume_at))}")
        return v("WAITING", "SCHEDULED", _block_reason(tid, b), waiting_on="scheduled")

    if status in ("blocked", "triage"):
        reason = _block_reason(tid, b)
        cause = "TRIAGE_PARKED" if status == "triage" else "NEEDS_HUMAN"
        if _subscribed(tid, b):  # the blocked/block_loop_detected event already woke that chat
            label = WHY.get(t.get("block_kind") or "", "a decision") if status == "blocked" else "triage decision"
            return v("WAITING", cause, reason, waiting_on=f"human: {label}")
        return v("STUCK", "UNSUBSCRIBED" if status == "blocked" else cause, reason, escalate=True)

    if status == "todo":
        open_parents = [p for p in b["parents"].get(tid, []) if b["status_of"].get(p) not in ("done", "archived")]
        if open_parents:
            return v("WAITING", "PARENT_WAIT", waiting_on=f"parent {', '.join(sorted(open_parents))}")
        if idle >= UNKNOWN_STALL_MINUTES * 60:
            return v("STUCK", "UNKNOWN_STALL", f"todo {_age(idle)} with every parent done", escalate=True)
        return v("PROGRESSING", "CLAIM_PENDING", "todo, promotion pending")

    # ready / review
    hold, hold_since = _hold(tid, b, now)
    walled = b["rate_limited"].get(t["assignee"], 0) >= QUOTA_EVENTS
    if walled or hold in ("rate_limited", "respawn_guarded:rate_limit_cooldown"):
        return v("WAITING", "QUOTA_WALL", f"{b['rate_limited'].get(t['assignee'], 0)} rate-limited runs for "
                 f"{t['assignee']} in {QUOTA_WINDOW_MINUTES}m", waiting_on=f"{t['assignee']} quota reset",
                 repair="quota_pause" if status == "ready" and walled and tid not in st.get("quota_pause", {}) else None)
    if hold == "respawn_guarded:blocker_auth":
        err = t.get("last_failure_error") or ""
        ended = [r for r in b["runs"][tid] if r.get("ended_at")]
        latest = ended[-1].get("outcome") if ended else None
        if STALE_GUARD_RE.search(err) and latest not in FAILURE_OUTCOMES:
            tries = st.get("repairs", {}).get(tid, {}).get("clear_guard", 0)
            return v("STUCK", "STALE_GUARD", f"guard holds on old error ({_short(err, 90)}); latest run {latest}",
                     repair=None if tries >= REPAIR_ATTEMPTS else "clear_guard",
                     escalate=tries >= REPAIR_ATTEMPTS, signature=signature(err))
        return v("STUCK", "AUTH_BLOCKED", err, escalate=True, signature=signature(err))
    if hold.startswith("per_profile_cap"):
        if now - hold_since >= AT_CAP_ESCALATE_MINUTES * 60:
            return v("STUCK", "AT_CAP", hold, escalate=True)
        return v("WAITING", "AT_CAP", hold, waiting_on=hold)
    if idle < CLAIM_GRACE_MINUTES * 60:
        return v("PROGRESSING", "CLAIM_PENDING", f"{status} {_age(idle)}")
    if status == "review" and idle >= ORPHAN_REVIEW_MINUTES * 60:
        return v("STUCK", "ORPHAN_REVIEW", f"review {_age(idle)}, no reviewer run{f', hold {hold}' if hold else ''}",
                 escalate=idle >= 2 * ORPHAN_REVIEW_MINUTES * 60)
    if idle >= UNKNOWN_STALL_MINUTES * 60:
        return v("STUCK", "UNKNOWN_STALL", f"{status} {_age(idle)}{f', hold {hold}' if hold else ''}",
                 escalate=True)
    return v("PROGRESSING", "CLAIM_PENDING", f"{status} {_age(idle)}")


def classify_board(b, now, st=None, watches=None) -> dict[str, Verdict]:
    """Every open card's verdict; a stuck card behind a stuck ancestor reports through that ancestor."""
    verdicts = {tid: classify(tid, b, now, st, watches) for tid in b["tasks"]}

    def stuck_ancestor(tid, seen=()):
        for p in b["parents"].get(tid, []):
            if p in seen:
                continue
            if p in verdicts and verdicts[p].state == "STUCK":
                return p
            found = stuck_ancestor(p, (*seen, p))
            if found:
                return found
        return None

    for tid, v in verdicts.items():
        root = stuck_ancestor(tid)
        if root and v.state != "PROGRESSING":
            verdicts[root].blocked_children.append(tid)
            if v.state == "STUCK" or v.cause == "PARENT_WAIT":
                verdicts[tid] = Verdict(tid, "WAITING", "PARENT_STUCK", f"root cause {root}: {verdicts[root].cause}",
                                        waiting_on=f"parent {root}", chain=v.chain)
    return verdicts


# ---------------------------------------------------------------- watches (PR / CI)

def poll_watch(watch: dict, gh: Callable[..., tuple[bool, str]]) -> str:
    """'pending' | 'pass' | 'fail' for one watch; 'pending' when gh cannot tell."""
    ok, out = gh("pr", "view", str(watch.get("ref")), "--json", "state,statusCheckRollup")
    if not ok:
        return "pending"
    data = _payload(out)
    checks = data.get("statusCheckRollup") or []
    conclusions = [(c.get("conclusion") or c.get("state") or "").upper() for c in checks]
    if any(c in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED") for c in conclusions):
        return "fail"
    if watch.get("kind") == "pr":
        return {"MERGED": "pass", "CLOSED": "fail"}.get(data.get("state", ""), "pending")
    if checks and all(c in ("SUCCESS", "NEUTRAL", "SKIPPED") for c in conclusions):
        return "pass"
    return "pending"


def _gh(*args: str) -> tuple[bool, str]:
    exe = shutil.which("gh")
    if not exe:
        return False, "gh not installed"
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return out.returncode == 0, out.stdout


# ---------------------------------------------------------------- acting

def clear_stale_guard(db: Path, tid: str) -> bool:
    """D3: the one direct write. Clears a superseded quota error so the respawn guard lets go.
    Every precondition is re-checked inside the write transaction."""
    conn = sqlite3.connect(db, timeout=10)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, last_failure_error, current_run_id, "
                "(SELECT outcome FROM task_runs WHERE task_id=tasks.id AND ended_at IS NOT NULL "
                " ORDER BY ended_at DESC LIMIT 1) FROM tasks WHERE id=?", (tid,)).fetchone()
            if not row or row[0] not in ("ready", "review") or row[2] is not None \
                    or not STALE_GUARD_RE.search(row[1] or "") or row[3] in FAILURE_OUTCOMES:
                return False
            conn.execute("UPDATE tasks SET last_failure_error=NULL WHERE id=? AND status IN ('ready','review') "
                         "AND current_run_id IS NULL", (tid,))
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _repair(v: Verdict, b, st, now, run, db) -> tuple[bool, str]:
    tid = v.card
    if v.repair == "reclaim":
        return run("kanban", "reclaim", tid, "--reason", f"delivery monitor: {v.evidence}")
    if v.repair == "clear_guard":
        ok = clear_stale_guard(db, tid)
        return ok, "cleared stale last_failure_error" if ok else "preconditions no longer hold"
    if v.repair == "quota_pause":
        ok, out = run("kanban", "schedule", tid, f"delivery monitor: {b['tasks'][tid]['assignee']} quota wall; "
                      f"retrying after {QUOTA_PAUSE_MINUTES}m")
        if ok:
            st.setdefault("quota_pause", {})[tid] = now + QUOTA_PAUSE_MINUTES * 60
        return ok, out
    if v.repair == "resume":
        ok, out = run("kanban", "unblock", tid, "--reason", "delivery monitor: quota pause elapsed")
        if ok:
            st.get("quota_pause", {}).pop(tid, None)
        return ok, out
    if v.repair == "archive":
        t = b["tasks"][tid]
        run("kanban", "comment", tid, f"FINAL: archived by the delivery monitor after {EXPIRE_ARCHIVE_DAYS} days "
            f"without a decision. Last reason: {_short(_block_reason(tid, b), 200)}. Workspace kept: "
            f"{t.get('workspace_path') or 'none'}. Revive with `hermes kanban unarchive` or resubmit.")
        return run("kanban", "archive", tid)
    return False, f"unknown repair {v.repair}"


def _escalate(v: Verdict, b, mode, run, notices) -> str:
    """Tell the origin chat. Returns how: 'block' or 'notice'."""
    t = b["tasks"][v.card]
    text = f"{v.cause}: {v.evidence or _short(t['title'], 80)}; next: {NEXT.get(v.cause, 'decide on the card')}"
    if v.blocked_children:
        text += f" ({len(v.blocked_children)} card(s) wait on this)"
    if mode == "act" and t["status"] in ("ready", "running"):
        ok, _ = run("kanban", "block", v.card, "--kind", "needs_input", _short(text, 480))
        if ok:
            return "block"
    chats = {(s.get("platform"), s.get("chat_id")) for s in b["subs"].get(v.card, [])}
    for sid in b["subs"].get(v.chain, []) if not chats else []:
        chats.add((sid.get("platform"), sid.get("chat_id")))
    for platform, chat in chats:
        notices.setdefault(str(chat), []).append(
            {"card": v.card, "title": _short(t["title"], 60), "text": _short(text, 400), "platform": platform})
    return "notice"


def unsubscribed_repairs(b) -> list[tuple[str, dict]]:
    """(card, sub) for open cards with no subscription whose chain root or a parent has one."""
    out = []
    for tid, t in b["tasks"].items():
        if b["subs"].get(tid) or not b["parents"].get(tid):
            continue
        for src in [*b["parents"][tid], _chain_root(tid, b)]:
            if b["subs"].get(src):
                out += [(tid, s) for s in b["subs"][src]]
                break
    return out


def tick(conn, now, st, watches, *, mode="observe", run=hermes, gh=_gh, db: Path = DB,
         home: Path = HERMES_HOME) -> tuple[dict, dict, list]:
    """One monitor pass: classify, repair, escalate, journal. Returns (verdicts, board, notices)."""
    b = load_board(conn, now, home)
    notices: list = []
    note_map: dict = {}

    # watches first: a resolved watch changes the card's verdict this tick
    for tid, w in list(watches.items()):
        if tid not in b["tasks"]:
            watches.pop(tid)
            continue
        result = "expired" if now > float(w.get("until") or now + 1) else poll_watch(w, gh)
        if result == "pending":
            continue
        watches.pop(tid)
        journal.record("watch.resolved", tid, result=result, ref=w.get("ref"), home=home)
        if result == "pass" and b["tasks"][tid]["status"] == "blocked" and mode == "act":
            run("kanban", "unblock", tid, "--reason", f"delivery monitor: {w.get('ref')} passed")
        elif result != "pass":
            cause = "WATCH_FAILED" if result == "fail" else "WATCH_EXPIRED"
            _escalate(Verdict(tid, "STUCK", cause, str(w.get("ref")), chain=_chain_root(tid, b)),
                      b, mode, run, note_map)

    verdicts = classify_board(b, now, st, watches)
    prev = st.get("cards", {})
    repairs = st.setdefault("repairs", {})
    escalated = st.setdefault("escalated", {})
    for tid, v in verdicts.items():
        old = prev.get(tid, {})
        if (old.get("state"), old.get("cause")) != (v.state, v.cause):
            journal.record("monitor.state", tid, chain=v.chain, state=v.label, cause=v.cause,
                           evidence=v.evidence, home=home)
        if v.repair:
            if mode == "act":
                ok, out = _repair(v, b, st, now, run, db)
                counts = repairs.setdefault(tid, {})
                counts[v.repair] = counts.get(v.repair, 0) + 1
                journal.record("monitor.repair", tid, chain=v.chain, cause=v.cause, repair=v.repair, ok=ok,
                               output=out, home=home)
            else:
                journal.record("monitor.would_repair", tid, chain=v.chain, cause=v.cause, repair=v.repair,
                               evidence=v.evidence, home=home)
        if v.escalate and v.state == "STUCK":
            key = f"{v.cause}:{v.signature}"
            if escalated.get(tid) != key:
                how = _escalate(v, b, mode, run, note_map)
                escalated[tid] = key
                if v.cause == "EXPIRING":
                    st.setdefault("expiry_warned", {}).setdefault(tid, now)
                journal.record("monitor.escalate", tid, chain=v.chain, cause=v.cause, how=how,
                               evidence=v.evidence, home=home)
                if v.cause in ("FAST_FAIL", "IDENTICAL_FAILURE"):
                    journal.record(f"waste.{v.cause.lower()}", tid, signature=v.signature, home=home)
    for tid in [k for k in escalated if k not in verdicts or verdicts[k].state != "STUCK"]:
        escalated.pop(tid)  # moving again: a later stall escalates afresh
    for tid in [k for k in st.get("expiry_warned", {}) if verdicts.get(k) is None
                or verdicts[k].cause not in ("EXPIRING", "EXPIRED")]:
        st["expiry_warned"].pop(tid)  # decided (or moved): a later expiry warns again

    for tid, sub in unsubscribed_repairs(b):
        args = ["kanban", "notify-subscribe", tid, "--platform", str(sub.get("platform")),
                "--chat-id", str(sub.get("chat_id"))]
        if sub.get("thread_id"):
            args += ["--thread-id", str(sub["thread_id"])]
        if sub.get("delivery_mode"):
            args += ["--delivery-mode", str(sub["delivery_mode"])]
        if mode == "act":
            ok, out = run(*args)
            journal.record("monitor.repair", tid, cause="UNSUBSCRIBED", repair="resubscribe", ok=ok, home=home)
        else:
            journal.record("monitor.would_repair", tid, cause="UNSUBSCRIBED", repair="resubscribe", home=home)

    # holds and outcomes, for the journal's numbers
    holds = {tid: _hold(tid, b, now)[0] for tid in b["tasks"]}
    holds = {k: h for k, h in holds.items() if h}
    for tid in set(holds) | set(st.get("holds", {})):
        if holds.get(tid) != st.get("holds", {}).get(tid):
            if st.get("holds", {}).get(tid):
                journal.record("hold.end", tid, reason=st["holds"][tid], home=home)
            if holds.get(tid):
                journal.record("hold.start", tid, reason=holds[tid], home=home)
    st["holds"] = holds
    last = st.get("last_tick_at") or now
    for r in conn.execute("SELECT id, status, (SELECT count(*) FROM task_runs WHERE task_id=tasks.id), "
                          "coalesce(completed_at, 0) - created_at FROM tasks WHERE completed_at >= ? "
                          "AND completed_at < ?", (last, now)):
        journal.record("card.outcome", r[0], outcome=r[1], runs=r[2], seconds=int(r[3] or 0), home=home)
    (quota_respawns,) = conn.execute("SELECT count(*) FROM task_events WHERE kind='rate_limited' "
                                     "AND created_at >= ? AND created_at < ?", (last, now)).fetchone()
    if quota_respawns:
        journal.record("waste.quota_respawn", None, count=quota_respawns, home=home)

    st["cards"] = {tid: {"state": v.state, "cause": v.cause, "label": v.label, "evidence": v.evidence,
                         "chain": v.chain, "waiting_on": v.waiting_on, "blocked_children": v.blocked_children,
                         "title": b["tasks"][tid]["title"], "status": b["tasks"][tid]["status"],
                         "since": prev.get(tid, {}).get("since") if (prev.get(tid, {}).get("state"),
                                  prev.get(tid, {}).get("cause")) == (v.state, v.cause) else now}
                   for tid, v in verdicts.items()}
    st["cards"] = {k: {**c, "since": c["since"] or now} for k, c in st["cards"].items()}
    st["last_tick_at"] = now
    st["mode"] = mode
    for chat, items in note_map.items():
        notices += [{**i, "chat_id": chat} for i in items]
    return verdicts, b, notices


# ---------------------------------------------------------------- digest (operator bot channel)

def engine_stalls(conn, now):
    """Board-wide problems that stop every card, reported once instead of per card."""
    since = now - ENGINE_WINDOW_MINUTES * 60
    stalls = {}
    crashes = conn.execute(
        "SELECT count(*), max(id) FROM task_events WHERE kind='crashed' AND created_at>=?",
        (since,)).fetchone()
    if crashes[0] >= ENGINE_EVENT_THRESHOLD:
        payload = conn.execute("SELECT payload FROM task_events WHERE id=?", (crashes[1],)).fetchone()
        tail = str(_payload(payload[0]).get("worker_output", ""))[-200:]
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


def dispatcher_stall(b, now, home: Path = HERMES_HOME):
    """Silent only when BOTH the tick telemetry and gateway.log are stale: one alone lies."""
    waiting = sum(1 for t in b["tasks"].values() if t["status"] in ("ready", "review"))
    last_tick = float(b["health"].get("last_tick_at") or 0)
    if not waiting or not last_tick or now - last_tick < DISPATCHER_SILENT_MINUTES * 60:
        return {}
    try:
        if now - (home / "logs" / "gateway.log").stat().st_mtime < DISPATCHER_SILENT_MINUTES * 60:
            return {}
    except OSError:
        pass
    return {"engine:dispatcher": {
        "line": f"**Dispatcher silent** — no tick for {_age(now - last_tick)} while {waiting} card(s) wait; "
                "check `hermes gateway status` (do not dispatch by hand: it bypasses ESTOP and caps)",
        "detail": "", "since": last_tick, "name": "dispatcher"}}


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


def card_stalls(verdicts, b, now, st=None):
    """Digest items: one per STUCK root cause (children are counted on the root), plus undecided blocks."""
    stalls = {}
    since = {k: c.get("since", now) for k, c in (st or {}).get("cards", {}).items()}
    for tid, v in verdicts.items():
        t = b["tasks"][tid]
        if v.state == "STUCK":
            why = f"{v.cause}: {NEXT.get(v.cause, 'needs a decision')}"
        elif v.cause in ("NEEDS_HUMAN", "TRIAGE_PARKED") and now - _moved_at(tid, b) >= UNKNOWN_STALL_MINUTES * 60:
            why = WHY.get(t.get("block_kind") or "", "parked in triage" if t["status"] == "triage" else "blocked")
        else:
            continue
        waiting = f" · {len(v.blocked_children)} waiting on it" if v.blocked_children else ""
        start = min(since.get(tid, now), _moved_at(tid, b))
        stalls[f"{tid}:{v.cause}"] = {
            "line": f"**{_short(t['title'], 70)}** (`{tid}`, {t['assignee'] or 'unassigned'})"
                    f" — {t['status']} {_age(now - start)}: {why}{waiting}",
            "detail": _short(v.evidence or _block_reason(tid, b)),
            "since": start, "name": _short(t["title"], 50)}
    return stalls


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
    if resolved and lines:  # a recovery alone wakes the coordinator for nothing: ride along with news
        names = ", ".join(seen[k].get("name", k) for k in resolved[:5])
        more = f" (+{len(resolved) - 5} more)" if len(resolved) > 5 else ""
        lines.append(f"✅ Moving again: {names}{more}")
    if current and (remind or (new and len(current) != state.get("count"))):
        oldest = min(v["since"] for v in current.values())
        lines.append(f"⏳ Stuck overall: {len(current)} item(s), oldest {_age(now - oldest)}")
    new_state = {
        "seen": {k: {"name": v["name"]} for k, v in current.items()},
        "reminded_at": now if (new or remind) else state.get("reminded_at", 0),
        "count": len(current),
    }
    return "\n".join(lines), new_state


def estop_engaged(home: Path = HERMES_HOME) -> bool:
    return (home / "ESTOP").exists()


def evaluate(home: Path = HERMES_HOME, now: Optional[float] = None) -> tuple[dict, dict]:
    """Read-only verdicts for callers outside the cron (delivery_status). Never writes."""
    now = now or time.time()
    conn = sqlite3.connect(f"file:{home / 'kanban.db'}?mode=ro", uri=True, timeout=5)
    try:
        b = load_board(conn, now, home)
        return classify_board(b, now, _load(home / "state" / "delivery-state.json"),
                              _load(home / "state" / "delivery-watches.json")), b
    finally:
        conn.close()


def main():
    if not DB.exists():
        return
    now = time.time()
    st = _load(STATE)
    if not st and LEGACY_STATE.exists():
        st = {"digest": _load(LEGACY_STATE)}
    watches = _load(WATCHES)
    mode = "observe" if estop_engaged() else monitor_mode()
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        verdicts, b, notices = tick(conn, now, st, watches, mode=mode)
        current = {**roster_stalls(now), **engine_stalls(conn, now), **dispatcher_stall(b, now),
                   **card_stalls(verdicts, b, now, st)}
    finally:
        conn.close()
    message, st["digest"] = build_digest(current, st.get("digest", {}), now)
    _save(STATE, st)
    _save(WATCHES, watches)
    if notices:
        pending = _load(NOTICES)
        for n in notices:
            pending.setdefault(n["chat_id"], []).append({**n, "at": now})
        _save(NOTICES, pending)
    if message:
        print(message)


if __name__ == "__main__":
    main()
