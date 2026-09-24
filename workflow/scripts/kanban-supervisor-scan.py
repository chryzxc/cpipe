#!/usr/bin/env python3
"""Kanban stall supervisor scan — deterministic, no LLM.

Detects the board conditions that stall the Bot workflow autonomously:
  1. blocked cards whose skills are not installed on the assignee profile
      (auto-fixable when the skill exists in the global catalog)
  2. cards referencing skills that exist nowhere (escalate)
  3. unassigned todo cards aging past a threshold (escalate to coordinator digest)
  4. review-lane cards the dispatcher never claims (REVIEW_STALLED)
  5. stale running claims past claim expiry (reclaim candidates)
  6. worker readiness failures: credentials/startup blockers (READINESS_BLOCKER),
      respawn loops past the engine failure limit (RESPAWN_LOOP), and terminal
      runs that produced no usable response (WORKER_EMPTY_RESULT)
  7. ready cards the engine dispatcher never promotes to running (READY_STUCK) —
      the gateway's own dispatcher logs this condition (gateway.log: "kanban
      dispatcher stuck: ready queue non-empty ... 0 workers spawned") but that
      warning never reaches the board or a human; this scan detects the same
      condition independently from the DB so it surfaces here instead. When the
      plugin's dispatch-tick telemetry says a card is only waiting behind the
      per-profile cap, it is reported as QUEUED_AT_CAP instead of a stall, and a
      gateway that stopped ticking is DISPATCHER_SILENT
  8. continuity: ESTOP pauses (PAUSED_BY_ESTOP), verdicts parked in block reasons
      (VERDICT_PARKED), blocks without a reason (BLOCKED_NO_REASON), done cards
      with no successor (ORPHANED_CHAIN), and cards parked in triage (TRIAGE_PARKED)

Output is consumed by the supervisor cron agent (monitor mode: unchanged
output suppresses the agent entirely). Exit 0 always.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DB = HERMES_HOME / "kanban.db"
GLOBAL_SKILLS = HERMES_HOME / "skills"
PROFILES = HERMES_HOME / "profiles"
TODO_AGING_HOURS = 24
QUEUE_AGING_MINUTES = 30
READY_STUCK_MINUTES = 15
REVIEW_STUCK_MINUTES = 15
DISPATCHER_SILENT_MINUTES = 5
ORPHAN_LIST_CAP = 8
COORDINATOR_WAKE_MINUTES = 15
HEARTBEAT_STALE_MINUTES = 5
WORKSPACE_CHECK_CAP = 32
WORKSPACE_CHECK_WORKERS = 8
REWORK_LOOP_THRESHOLD = 4
UNSUBSCRIBED_BLOCK_GRACE_MINUTES = 30
ORPHANED_CHAIN_WINDOW_HOURS = 48
# Mirrors the engine circuit breaker (DEFAULT_FAILURE_LIMIT in
# kanban_db_dispatch.py): past this many consecutive failures the engine
# stops respawning, so the board must surface a bounded recovery decision.
RESPAWN_LOOP_THRESHOLD = 2
EMPTY_RUN_OUTCOMES = {"completed", "crashed", "timed_out", "gave_up", "spawn_failed"}
AUTH_FAILURE_RE = re.compile(
    r"\b40[13]\b"
    r"|unauthorized"
    r"|forbidden"
    r"|invalid[ _-]api[ _-]key"
    r"|missing[ _-]api[ _-]key"
    r"|api[ _-]key.{0,24}(?:invalid|missing|required)"
    r"|not authenticated"
    r"|unauthenticated"
    r"|no stored credentials"
    r"|credentials?.{0,16}(?:missing|invalid|required|not found)"
    r"|authentication (?:required|failed)"
    r"|failed to authenticate",
    re.IGNORECASE,
)
PR_URL_RE = re.compile(r"github\.com/[\w.-]+/[\w.-]+/(pull|pulls)/\d+", re.IGNORECASE)
# A reviewer that finished its review but could not record the verdict natively
# (standalone review card claimed from `ready`, so kanban_request_changes rejects
# it with "active run was not claimed from review") parks the verdict in the
# block reason. The verdict is terminal; the card is not waiting on anyone.
VERDICT_RE = re.compile(r"\b(REQUEST_CHANGES|APPROVED)\b")
VERDICT_CONTEXT_RE = re.compile(
    r"not claimed from review|request[-_ ]changes transition|kanban_request_changes|verdict",
    re.IGNORECASE,
)
CAPACITY_BLOCK_RE = re.compile(
    r"active[- ]worker cap\b|dispatch[-_]blocked\b|at capacity\b|"
    r"profile is busy|capacity is (?:full|exhausted|at)",
    re.IGNORECASE,
)


def _has_pr_reference(task_id: str, continuation_body: str, conn) -> bool:
    """True when the card carries any pull-request URL or an explicit `pr:` note."""
    if PR_URL_RE.search(continuation_body or ""):
        return True
    row = conn.execute("SELECT title, body FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row and PR_URL_RE.search(f"{row['title'] or ''} {row['body'] or ''}"):
        return True
    marked = conn.execute(
        "SELECT 1 FROM task_comments WHERE task_id=? AND (body LIKE '%pr:%' OR body LIKE '%pull/%') "
        "LIMIT 1", (task_id,)).fetchone()
    return bool(marked)


def per_profile_cap():
    """Engine per-profile concurrency cap from config.yaml; None when unreadable."""
    try:
        text = (HERMES_HOME / "config.yaml").read_text()
    except OSError:
        return None
    match = re.search(r"max_in_progress_per_profile:\s*(\d+)", text)
    return int(match.group(1)) if match else None
NEXUS_ACTION_KEYWORDS = ("plan_amendment", "needs_assistance", "re-specif",
                          "respecify", "nexus triage", "blocked: plan",
                          "blocked: needs", "workspace_invalid")

RUN_STATES = {"blocked": "BLOCKED", "todo": "TODO", "review": "REVIEW",
              "running": "RUNNING", "in_progress": "RUNNING"}


def profile_skill_dirs(profile: str) -> set[str]:
    """Skill directory names visible to a profile (any category depth)."""
    root = PROFILES / profile / "skills"
    found: set[str] = set()
    if not root.is_dir():
        return found
    for category in root.iterdir():
        if not category.is_dir() or category.name.startswith("."):
            continue
        found.add(category.name)  # category-level skills
        for skill in category.iterdir():
            if skill.is_dir() and not skill.name.startswith("."):
                found.add(skill.name)
    roles = root / "team-roles"
    if roles.is_dir():
        for role in roles.iterdir():
            if role.is_dir():
                found.add(role.name)
    return found


def global_skill_exists(name: str) -> bool:
    if (GLOBAL_SKILLS / name).is_dir():
        return True
    if not GLOBAL_SKILLS.is_dir():
        return False
    for category in GLOBAL_SKILLS.iterdir():
        if category.is_dir() and (category / name).is_dir():
            return True
    return False


def _review_scope_key(title: str) -> str:
    """Coarse scope key for review cards: PR numbers win, else repo keywords."""
    import re
    pr = re.search(r"#(\d{3,})", title)
    if pr:
        return f"pr-{pr.group(1)}"
    words = [w for w in re.split(r"[^A-Za-z]+", title.lower()) if len(w) > 4][:3]
    return "-".join(words) or title.lower()[:20]


def _is_git_workspace(path: str) -> bool:
    workspace = Path(path).resolve()
    if not workspace.is_dir():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == workspace


def _column(row, name, default=None):
    """Read a column that may be absent on older database schemas."""
    return row[name] if name in row.keys() else default


def _workspace_head(path):
    """Bounded recovery identity for a workspace: path@short-sha (or bare path)."""
    if not path:
        return "no workspace"
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return path
    if result.returncode == 0 and result.stdout.strip():
        return f"{path}@{result.stdout.strip()}"
    return path


def _has_marker(conn, task_id: str, marker: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM task_comments WHERE task_id=? AND body LIKE ? LIMIT 1",
        (task_id, f"%{marker}%")).fetchone()
    return bool(row)


def estop_state():
    """Read the global emergency-stop sentinel (``$HERMES_HOME/ESTOP``) deterministically.

    Mirrors ``agent.estop.get_state`` without importing the engine: one uncached ``os.stat``
    plus an optional JSON read. Returns None when not engaged, or a dict with ``reason``/
    ``engaged_at`` (both possibly None for a corrupt/empty sentinel — fail safe, still engaged).
    """
    path = HERMES_HOME / "ESTOP"
    try:
        if not path.exists():
            return None
    except OSError:
        return {"reason": None, "engaged_at": None}
    state = {"reason": None, "engaged_at": None}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            state = {"reason": raw.get("reason") or None, "engaged_at": raw.get("engaged_at") or None}
    except (OSError, ValueError, TypeError):
        pass
    return state


def _latest_terminal_run(conn, task_id: str):
    """Latest finished run, or None when runs are absent/unreadable (old schema)."""
    try:
        return conn.execute(
            "SELECT outcome, summary FROM task_runs "
            "WHERE task_id=? AND outcome IS NOT NULL AND ended_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
    except sqlite3.OperationalError:
        return None


def _latest_event_reason(conn, task_id: str, kinds=("blocked",)) -> str:
    """``reason`` from the latest event of the given kinds ('' when absent)."""
    marks = ",".join("?" * len(kinds))
    ev = conn.execute(
        f"SELECT payload FROM task_events WHERE task_id=? AND kind IN ({marks}) "
        "ORDER BY id DESC LIMIT 1", (task_id, *kinds)).fetchone()
    if not ev:
        return ""
    try:
        return str(json.loads(ev["payload"] or "{}").get("reason") or "")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return ""


def _child_count(conn, task_id: str) -> int:
    """Linked successor cards (engine DAG); 0 on schemas without task_links."""
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM task_links WHERE parent_id=?", (task_id,)).fetchone()["c"]
    except sqlite3.OperationalError:
        return 0


def _review_entered_at(conn, task, now: float) -> float:
    """When a card entered the review lane: its review_requested event, else the
    end of its latest run, else when it started. First-tick estimate only; later
    ticks carry review_since forward in the progress state."""
    try:
        ev = conn.execute(
            "SELECT MAX(created_at) AS at FROM task_events WHERE task_id=? AND kind='review_requested'",
            (task["id"],)).fetchone()
        if ev and ev["at"]:
            return float(ev["at"])
        run = conn.execute(
            "SELECT ended_at FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task["id"],)).fetchone()
        if run and run["ended_at"]:
            return float(run["ended_at"])
    except sqlite3.OperationalError:
        pass
    return float(task["started_at"] or task["created_at"] or now)


def dispatch_health():
    """Engine dispatch telemetry written by the plugin's on_kanban_dispatch_tick hook."""
    try:
        data = json.loads((HERMES_HOME / "logs" / "dispatch-health.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def review_dispatch_enabled() -> bool:
    try:
        text = (HERMES_HOME / "config.yaml").read_text()
    except OSError:
        return True
    return not re.search(r"^\s*review_dispatch:\s*(false|no|off)\b", text, re.MULTILINE | re.IGNORECASE)


def main() -> None:
    if not DB.exists():
        print("NO BOARD")
        return
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = time.time()
    findings: list[str] = []

    tasks = conn.execute(
        "SELECT * FROM tasks "
        "WHERE status IN ('ready','blocked','todo','review','running','in_progress')"
    ).fetchall()

    paused = estop_state()
    if paused is not None:
        held = [t for t in tasks if t["status"] in ("ready", "review")
                or (t["status"] == "todo" and t["assignee"]
                    and (now - (t["created_at"] or now)) / 60 >= QUEUE_AGING_MINUTES)]
        if held:
            reason = f" (reason: {paused['reason']})" if paused.get("reason") else ""
            since = f" since {paused['engaged_at']}" if paused.get("engaged_at") else ""
            # engaged_at (not an age) keeps stdout byte-stable across ticks for the monitor hash.
            findings.append(
                f"PAUSED_BY_ESTOP · {len(held)} card(s) intentionally held by the global "
                f"emergency stop{since}{reason} — not a dispatcher failure; ESTOP also pauses cron, "
                f"so only the operator can lift it with `hermes resume`; never bypass it with a "
                f"manual `hermes kanban dispatch`")

    review_cards: dict[str, list] = {}
    for t in tasks:
        if t["status"] == "blocked":
            title = (t["title"] or "").lower()
            if "review" in title:
                review_cards.setdefault(_review_scope_key(t["title"] or ""), []).append(
                    (t["created_at"] or 0, t["id"], (t["title"] or "")[:60]))

    for scope, cards in review_cards.items():
        if len(cards) > 1:
            cards.sort()
            for _, tid, title in cards[:-1]:
                findings.append(
                    f"SUPERSEDED_REVIEW · {tid} · newer review exists for same scope '{scope}' · {title}")

    verdict_parked: set[str] = set()
    for t in tasks:
        if t["status"] == "blocked":
            last = conn.execute(
                "SELECT body FROM task_comments WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (t["id"],)).fetchone()
            body = (last["body"] or "") if last else ""
            head = body.lstrip()[:60]
            if head.startswith(("REQUEST_CHANGES", "APPROVED", "NEEDS_ASSISTANCE", "Coordinator reconciliation", "Nexus reconciliation")):
                findings.append(
                    f"VERDICT_PARKED · {t['id']} · terminal verdict already recorded but card still blocked · {head[:50]}")
                verdict_parked.add(t["id"])
                continue
            reason = _latest_event_reason(conn, t["id"])
            verdict = VERDICT_RE.search(reason)
            if verdict and VERDICT_CONTEXT_RE.search(reason):
                findings.append(
                    f"VERDICT_PARKED · {t['id']} · {verdict.group(1)} recorded in the block reason — the "
                    f"native review transition was unavailable (standalone review card) · "
                    f"{reason.strip()[:50]}")
                verdict_parked.add(t["id"])

    # Coordinator-action blocked reasons (legacy "Nexus" match kept for old comments) have no consumer; they park until a human asks.
    for t in tasks:
        if t["status"] != "blocked" or t["id"] in verdict_parked:
            continue
        ev = conn.execute(
            "SELECT payload, created_at FROM task_events WHERE task_id=? AND kind='blocked' "
            "ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
        reason = ""
        basis = ev["created_at"] if ev else (t["created_at"] or 0)
        if ev:
            try:
                reason = (json.loads(ev["payload"] or "{}").get("reason") or "").lower()
            except (json.JSONDecodeError, TypeError):
                reason = ""
        cm = conn.execute(
            "SELECT body, created_at FROM task_comments WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (t["id"],)).fetchone()
        if cm and cm["created_at"] and cm["created_at"] > basis:
            basis = cm["created_at"]
            if not reason:
                reason = (cm["body"] or "").lower()
        if not reason:
            # Nobody can act on a card whose blocker was never written down.
            if not _has_marker(conn, t["id"], "blocked_no_reason"):
                findings.append(
                    f"BLOCKED_NO_REASON · {t['id']} · blocked without a recorded reason or comment · "
                    f"{(t['title'] or '')[:60]}")
            continue
        if any(k in reason for k in NEXUS_ACTION_KEYWORDS):
            age_m = (now - basis) / 60
            if age_m < COORDINATOR_WAKE_MINUTES:
                continue
            recent_wake = conn.execute(
                "SELECT 1 FROM task_comments WHERE task_id=? AND body LIKE '%coordinator_wake%' "
                "AND created_at > ? LIMIT 1", (t["id"], now - 3600)).fetchone()
            if recent_wake:
                continue
            findings.append(
                f"COORDINATOR_WAKE · {t['id']} · blocked {age_m:.0f}m awaiting coordinator action "
                f"({reason[:48]}) · {(t['title'] or '')[:60]}")

    # Coordination agents must never block ready work for capacity; the dispatcher owns concurrency.
    cap = per_profile_cap()
    if cap is not None:
        running_by_assignee: dict[str, int] = {}
        for t in tasks:
            if t["status"] in ("running", "in_progress") and t["assignee"]:
                running_by_assignee[t["assignee"]] = running_by_assignee.get(t["assignee"], 0) + 1
        for t in tasks:
            if t["status"] != "blocked" or t["id"] in verdict_parked:
                continue
            ev = conn.execute(
                "SELECT payload FROM task_events WHERE task_id=? AND kind='blocked' "
                "ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
            if not ev:
                continue
            try:
                reason = (json.loads(ev["payload"] or "{}").get("reason") or "")
            except (json.JSONDecodeError, TypeError):
                continue
            if not CAPACITY_BLOCK_RE.search(reason):
                continue
            running = running_by_assignee.get(t["assignee"] or "", 0)
            if running >= cap:
                continue
            findings.append(
                f"CAPACITY_HOLD · {t['id']} · blocked on claimed capacity but {running} running "
                f"< cap {cap} · {(t['title'] or '')[:60]}")

    for t in tasks:
        if t["status"] not in ("ready", "review", "running", "in_progress"):
            continue
        cycles = conn.execute(
            "SELECT COUNT(*) AS c FROM task_runs WHERE task_id=? AND outcome='changes_requested'",
            (t["id"],)).fetchone()["c"]
        if cycles < REWORK_LOOP_THRESHOLD:
            continue
        nudged = conn.execute(
            "SELECT 1 FROM task_comments WHERE task_id=? AND body LIKE '%rework_loop%' LIMIT 1",
            (t["id"],)).fetchone()
        if nudged:
            continue
        findings.append(
            f"REWORK_LOOP · {t['id']} · {cycles} review cycles without completion · {(t['title'] or '')[:60]}")

    active_cards = [t for t in tasks if t["status"] in
                    ("todo", "ready", "review", "running", "in_progress")]
    titles = {t["id"]: (t["title"] or "")[:60] for t in tasks}
    subscribed: dict[str, bool] | None = None
    try:
        subs = {row["task_id"]: row["modes"]
                for row in conn.execute(
                    "SELECT task_id, GROUP_CONCAT(delivery_mode) AS modes FROM kanban_notify_subs "
                    "GROUP BY task_id")}
        subscribed = {tid: ("wake" in (modes or "")) for tid, modes in subs.items()}
    except sqlite3.OperationalError:
        subscribed = None
    for t in active_cards:
        if t["status"] == "todo" and not t["assignee"]:
            continue
        if subscribed is None or subscribed.get(t["id"]):
            continue
        marker = conn.execute(
            "SELECT created_at FROM task_comments WHERE task_id=? AND body LIKE '%unsubscribed_card%' "
            "ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
        if marker is None:
            findings.append(
                f"UNSUBSCRIBED_CARD · {t['id']} · {t['status']} card has no wake-capable notify subscription · "
                f"{titles[t['id']]}")
        elif (t["status"] == "ready"
              and marker["created_at"] is not None
              and (now - marker["created_at"]) / 60 >= UNSUBSCRIBED_BLOCK_GRACE_MINUTES):
            findings.append(
                f"UNSUBSCRIBED_BLOCK · {t['id']} · ready and unattended for "
                f"{(now - marker['created_at']) / 60:.0f}m — blocking fail-closed · {titles[t['id']]}")

    recent_done = conn.execute(
        "SELECT id, title, completed_at FROM tasks "
        "WHERE status='done' AND completed_at IS NOT NULL AND completed_at > ? "
        "ORDER BY completed_at DESC", (now - ORPHANED_CHAIN_WINDOW_HOURS * 3600,)).fetchall()
    orphans_unlisted = 0
    for t in recent_done:
        waked = conn.execute(
            "SELECT 1 FROM task_comments WHERE task_id=? AND body LIKE '%orphan_wake%' "
            "AND created_at >= ? LIMIT 1", (t["id"], t["completed_at"])).fetchone()
        continuation = conn.execute(
            "SELECT body FROM task_comments WHERE task_id=? AND body LIKE 'CONTINUATION:%' "
            "AND created_at >= ? ORDER BY id DESC LIMIT 1", (t["id"], t["completed_at"])).fetchone()
        if not continuation:
            # A linked successor IS the continuation: the engine promotes it when this
            # parent is done, with no coordinator turn in the critical path.
            if not waked and not _child_count(conn, t["id"]):
                listed = sum(1 for f in findings if f.startswith("ORPHANED_CHAIN · "))
                if listed < ORPHAN_LIST_CAP:
                    findings.append(
                        f"ORPHANED_CHAIN · {t['id']} · done without a recorded continuation decision · "
                        f"{(t['title'] or '')[:60]}")
                else:
                    orphans_unlisted += 1
            continue
        marker = conn.execute(
            "SELECT 1 FROM task_comments WHERE task_id=? AND body LIKE '%pr_pending%' "
            "AND created_at >= ? LIMIT 1", (t["id"], t["completed_at"])).fetchone()
        marker_text = (continuation["body"] or "").lower()
        if ("final report" in marker_text or "promote gate" in marker_text) \
                and not marker \
                and not _has_pr_reference(t["id"], continuation["body"], conn):
            findings.append(
                f"PR_PENDING · {t['id']} · reviewed implementation without a PR successor · "
                f"{(t['title'] or '')[:60]}")
    if orphans_unlisted:
        # Bounded prompt: later ticks list the rest once these carry orphan_wake.
        findings.append(
            f"ORPHANED_CHAIN_MORE · {orphans_unlisted} more orphaned card(s) beyond the "
            f"{ORPHAN_LIST_CAP}-card cap will be listed on later ticks")

    # triage: the engine's block-loop breaker (same block kind re-raised past the
    # recurrence limit) or an explicit human-triage park. Nothing dispatches triage,
    # so without this signal the card is silent forever.
    try:
        triage_cards = conn.execute(
            "SELECT id, title FROM tasks WHERE status='triage' ORDER BY created_at").fetchall()
    except sqlite3.OperationalError:
        triage_cards = []
    triage_unlisted = 0
    for t in triage_cards:
        if _has_marker(conn, t["id"], "triage_parked"):
            continue
        if sum(1 for f in findings if f.startswith("TRIAGE_PARKED · ")) >= ORPHAN_LIST_CAP:
            triage_unlisted += 1
            continue
        reason = _latest_event_reason(conn, t["id"], ("block_loop_detected", "blocked"))
        findings.append(
            f"TRIAGE_PARKED · {t['id']} · parked in triage, nothing dispatches it · "
            f"{(reason.strip() or 'no reason recorded')[:60]} · {(t['title'] or '')[:60]}")
    if triage_unlisted:
        findings.append(
            f"TRIAGE_PARKED_MORE · {triage_unlisted} more triage card(s) beyond the "
            f"{ORPHAN_LIST_CAP}-card cap will be listed on later ticks")

    readiness_signals = 0
    for t in tasks:
        tid = t["id"]
        title = (t["title"] or "")[:60]
        failures = _column(t, "consecutive_failures", 0) or 0
        failure_text = (t["last_failure_error"] or "").strip()
        workspace = t["workspace_path"]

        if failures >= 1 and failure_text and AUTH_FAILURE_RE.search(failure_text) \
                and not _has_marker(conn, tid, "readiness_blocker"):
            findings.append(
                f"READINESS_BLOCKER · {tid} · profile '{t['assignee'] or 'unassigned'}' cannot "
                f"authenticate or start — resolve provider credentials before re-dispatch · "
                f"workspace {_workspace_head(workspace)} · {failure_text[:60]} · {title}")
            readiness_signals += 1
            continue

        if failures >= RESPAWN_LOOP_THRESHOLD and not _has_marker(conn, tid, "respawn_bounded"):
            findings.append(
                f"RESPAWN_LOOP · {tid} · {failures} consecutive failures without completion — "
                f"stop respawning and record one recovery decision preserving workspace "
                f"{_workspace_head(workspace)} · {failure_text[:60] or 'identical startup failure'} · {title}")
            readiness_signals += 1
            continue

        run = _latest_terminal_run(conn, tid)
        if run is None or run["outcome"] not in EMPTY_RUN_OUTCOMES:
            continue
        summary = (run["summary"] or "").strip() if "summary" in run.keys() else None
        if summary is None:
            continue
        if summary or (_column(t, "result", None) or "").strip():
            continue
        if _has_marker(conn, tid, "worker_empty_result"):
            continue
        findings.append(
            f"WORKER_EMPTY_RESULT · {tid} · {run['outcome']} run returned no usable response — "
            f"route to one bounded recovery decision preserving workspace "
            f"{_workspace_head(workspace)} · {title}")
        readiness_signals += 1

    progress_state_path = HERMES_HOME / "logs" / "supervisor-progress-state.json"
    previous: dict[str, dict] = {}
    try:
        previous = json.loads(progress_state_path.read_text())
    except (OSError, json.JSONDecodeError):
        previous = {}
    current: dict[str, dict] = {}
    for t in active_cards:
        last_comment = conn.execute(
            "SELECT MAX(id) AS m FROM task_comments WHERE task_id=?", (t["id"],)).fetchone()
        entry = {"status": t["status"], "hb": last_comment["m"] or 0}
        if t["status"] in ("ready", "review"):
            # ready_since / review_since: how long the card has waited in its dispatch lane.
            since_key = f"{t['status']}_since"
            prior = previous.get(t["id"])
            if prior and prior.get("status") == t["status"] and prior.get(since_key):
                entry[since_key] = prior[since_key]
            elif t["status"] == "review":
                entry[since_key] = _review_entered_at(conn, t, now)
            else:
                entry[since_key] = now
        current[t["id"]] = entry
    if previous:
        for tid in sorted(set(previous) | set(current)):
            old = previous.get(tid)
            state = current.get(tid)
            if old is None or state is None:
                if state is None and old is not None:
                    row = conn.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
                    if row and row["status"] != old.get("status"):
                        findings.append(
                            f"PROGRESS_DELTA · {tid} · {old.get('status')}→{row['status']} · {titles.get(tid, tid)}")
                continue
            if old.get("status") != state["status"]:
                findings.append(
                    f"PROGRESS_DELTA · {tid} · {old.get('status')}→{state['status']} · {titles.get(tid, tid)}")
    try:
        (HERMES_HOME / "logs").mkdir(parents=True, exist_ok=True)
        progress_state_path.write_text(json.dumps(current, indent=2, sort_keys=True))
    except OSError:
        pass

    ready_paths = [t["workspace_path"] for t in tasks
                   if t["status"] == "ready" and t["workspace_path"]]
    checked_paths = ready_paths[:WORKSPACE_CHECK_CAP]
    if checked_paths:
        with ThreadPoolExecutor(max_workers=WORKSPACE_CHECK_WORKERS) as pool:
            checked = dict(zip(checked_paths, pool.map(_is_git_workspace, checked_paths)))
    else:
        checked = {}
    workspace_unchecked = len(ready_paths) - len(checked_paths)
    if workspace_unchecked > 0:
        findings.append(
            f"WORKSPACE_CAP · {workspace_unchecked} ready workspace(s) beyond the "
            f"{WORKSPACE_CHECK_CAP}-card cap were not checked this scan")

    for t in tasks:
        tid, title = t["id"], (t["title"] or "")[:60]
        assignee, status = t["assignee"], t["status"]

        workspace_error = (t["last_failure_error"] or "").lower()
        workspace = t["workspace_path"]
        if status == "ready" and (
            (workspace and workspace in checked and not checked[workspace])
            or (not workspace and "not inside a git repo" in workspace_error)
        ):
            findings.append(
                f"WORKSPACE_INVALID · {tid} · ready card workspace is not a Git root · {title}"
            )
            continue

        if status == "todo" and not assignee:
            age_h = (now - (t["created_at"] or now)) / 3600
            if age_h >= TODO_AGING_HOURS:
                findings.append(f"UNASSIGNED_TODO · {tid} · aging {age_h:.0f}h · {title}")
            continue

        if status in ("todo", "ready") and "token_budget" not in (t["body"] or ""):
            nudged = conn.execute(
                "SELECT 1 FROM task_comments WHERE task_id=? AND body LIKE '%token_budget%' LIMIT 1",
                (tid,)).fetchone()
            if not nudged:
                findings.append(
                    f"CARD_NO_BUDGET · {tid} · card created without a token_budget · {title}")

        if status == "todo" and assignee:
            age_m = (now - (t["created_at"] or now)) / 60
            if age_m >= QUEUE_AGING_MINUTES and paused is None:
                findings.append(
                    f"QUEUE_AGING · {tid} · assigned to {assignee} but unstarted {age_m:.0f}m · {title}")
            continue

        # skill availability on the assignee profile
        try:
            card_skills = json.loads(t["skills"] or "[]")
        except (json.JSONDecodeError, TypeError):
            card_skills = []
        if assignee and card_skills:
            installed = profile_skill_dirs(assignee)
            for s in card_skills:
                if s in installed:
                    continue
                if global_skill_exists(s):
                    findings.append(
                        f"AUTO_FIX_INSTALL_SKILL · {tid} · skill '{s}' missing on profile '{assignee}' "
                        f"but present in global catalog · {title}")
                else:
                    findings.append(
                        f"ESCALATE_UNKNOWN_SKILL · {tid} · skill '{s}' exists nowhere; "
                        f"strip from card or install source · {title}")

        if status in ("running", "in_progress"):
            hb = t["last_heartbeat_at"]
            heartbeat_basis = hb or t["started_at"] or t["created_at"]
            if heartbeat_basis and (now - heartbeat_basis) / 60 >= HEARTBEAT_STALE_MINUTES:
                findings.append(
                    f"HEARTBEAT_STALE · {tid} · no heartbeat {((now-heartbeat_basis)/60):.0f}m · {title}")
            row = conn.execute(
                "SELECT claim_expires FROM task_runs WHERE task_id=? "
                "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
            expires = row["claim_expires"] if row and row["claim_expires"] else None
            if expires and expires < now:
                findings.append(f"STALE_CLAIM · {tid} · claim expired {((now-expires)/60):.0f}m ago · {title}")

    # READY_STUCK / REVIEW_STALLED: the engine dispatcher (gateway, 15s ticks) logs
    # "kanban dispatcher stuck ... 0 workers spawned" to gateway.log but nothing turns
    # that into a board-visible signal. Detect it independently from ready_since /
    # review_since (tracked above across ticks, not from row creation time — a card
    # can sit blocked for days before becoming ready). The plugin's dispatch-tick
    # telemetry (logs/dispatch-health.json) names why the engine held a card, so a
    # card that is merely queued behind the per-profile cap is reported as
    # QUEUED_AT_CAP (informational) instead of a stall.
    if paused is None:
        health = dispatch_health() or {}
        holds = health.get("holds") or {}
        review_lane = review_dispatch_enabled()
        waiting = 0
        for t in tasks:
            status = t["status"]
            if status not in ("ready", "review"):
                continue
            if status == "review" and not review_lane:
                continue
            waiting += 1
            workspace = t["workspace_path"]
            if workspace and workspace in checked and not checked[workspace]:
                continue  # already explained by WORKSPACE_INVALID
            since = current.get(t["id"], {}).get(f"{status}_since", now)
            age_m = (now - since) / 60
            threshold = READY_STUCK_MINUTES if status == "ready" else REVIEW_STUCK_MINUTES
            if age_m < threshold:
                continue
            title = (t["title"] or "")[:60]
            hold = (holds.get(t["id"]) or {}).get("reason") or ""
            if hold.startswith("per_profile_cap"):
                findings.append(
                    f"QUEUED_AT_CAP · {t['id']} · {status} card queued behind {hold} — "
                    f"dispatches when a slot frees · {title}")
                continue
            note = f" · engine hold: {hold}" if hold else ""
            if status == "ready":
                findings.append(
                    f"READY_STUCK · {t['id']} · ready {age_m:.0f}m without the dispatcher "
                    f"promoting it to running — run `hermes kanban dispatch` and check profile "
                    f"health (venv, PATH, credentials) if it recurs{note} · {title}")
            else:
                findings.append(
                    f"REVIEW_STALLED · {t['id']} · review requested {age_m / 60:.0f}h ago, "
                    f"no reviewer dispatched{note} · {title}")
        last_tick = health.get("last_tick_at")
        if waiting and last_tick and (now - float(last_tick)) / 60 >= DISPATCHER_SILENT_MINUTES:
            ticked = datetime.fromtimestamp(float(last_tick), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            findings.append(
                f"DISPATCHER_SILENT · gateway dispatcher last ticked at {ticked} while {waiting} "
                f"ready/review card(s) wait — check `hermes gateway status` and "
                f"kanban.dispatch_in_gateway")

    conn.close()

    busy = any(t["status"] in ("ready", "running", "in_progress", "review") for t in tasks)
    if not busy:
        orphans = sum(1 for f in findings if f.startswith("ORPHANED_CHAIN"))
        awaiting = sum(1 for f in findings if f.startswith("COORDINATOR_WAKE"))
        readiness = sum(1 for f in findings if f.startswith(
            ("READINESS_BLOCKER", "RESPAWN_LOOP", "WORKER_EMPTY_RESULT")))
        if orphans:
            classification = "done-with-orphans"
        elif awaiting:
            classification = f"awaiting decisions ({awaiting} blocked)"
        elif readiness:
            classification = f"worker readiness ({readiness} cards)"
        else:
            classification = "intentional (no open signals)"
        findings.append(f"IDLE_BOARD · {classification}")

    # Identical findings must yield byte-identical stdout: cron monitor mode hashes it to skip the agent on idle ticks.
    print("===== KANBAN STALL SCAN =====")
    if findings:
        for f in findings:
            print(f)
        print(f"total_signals: {len(findings)}")
    else:
        print("NO FINDINGS")
    # Trailing state hash input: the findings block above IS the monitor hash basis.


if __name__ == "__main__":
    main()
