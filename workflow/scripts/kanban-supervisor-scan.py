#!/usr/bin/env python3
"""Kanban stall supervisor scan — deterministic, no LLM.

Detects the board conditions that stall the Bot workflow autonomously:
  1. blocked cards whose skills are not installed on the assignee profile
     (auto-fixable when the skill exists in the global catalog)
  2. cards referencing skills that exist nowhere (escalate)
  3. unassigned todo cards aging past a threshold (escalate to coordinator digest)
  4. review-requested cards with no reviewer activity (dispatch gap)
  5. stale running claims past claim expiry (reclaim candidates)

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
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DB = HERMES_HOME / "kanban.db"
GLOBAL_SKILLS = HERMES_HOME / "skills"
PROFILES = HERMES_HOME / "profiles"
TODO_AGING_HOURS = 24
QUEUE_AGING_MINUTES = 30
COORDINATOR_WAKE_MINUTES = 15
HEARTBEAT_STALE_MINUTES = 5
WORKSPACE_CHECK_CAP = 32
WORKSPACE_CHECK_WORKERS = 8
REWORK_LOOP_THRESHOLD = 4
UNSUBSCRIBED_BLOCK_GRACE_MINUTES = 30
ORPHANED_CHAIN_WINDOW_HOURS = 48
PR_URL_RE = re.compile(r"github\.com/[\w.-]+/[\w.-]+/(pull|pulls)/\d+", re.IGNORECASE)
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


def main() -> None:
    if not DB.exists():
        print("NO BOARD")
        return
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = time.time()
    findings: list[str] = []

    tasks = conn.execute(
        "SELECT id, title, status, assignee, skills, body, created_at, started_at, "
        "last_heartbeat_at, workspace_path, last_failure_error FROM tasks "
        "WHERE status IN ('ready','blocked','todo','review','running','in_progress')"
    ).fetchall()

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
            continue
        if any(k in reason for k in NEXUS_ACTION_KEYWORDS):
            age_m = (now - basis) / 60
            if age_m >= COORDINATOR_WAKE_MINUTES:
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
    for t in recent_done:
        continuation = conn.execute(
            "SELECT body FROM task_comments WHERE task_id=? AND body LIKE 'CONTINUATION:%' "
            "AND created_at >= ? ORDER BY id DESC LIMIT 1", (t["id"], t["completed_at"])).fetchone()
        if not continuation:
            findings.append(
                f"ORPHANED_CHAIN · {t['id']} · done without a recorded continuation decision · "
                f"{(t['title'] or '')[:60]}")
            continue
        marker = conn.execute(
            "SELECT 1 FROM task_comments WHERE task_id=? AND body LIKE '%pr_pending%' LIMIT 1",
            (t["id"],)).fetchone()
        marker_text = (continuation["body"] or "").lower()
        if ("final report" in marker_text or "promote gate" in marker_text) \
                and not marker and not _has_pr_reference(t["id"], continuation["body"], conn):
            findings.append(
                f"PR_PENDING · {t['id']} · reviewed implementation without a PR successor · "
                f"{(t['title'] or '')[:60]}")

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
        current[t["id"]] = {"status": t["status"], "hb": last_comment["m"] or 0}
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
            elif old.get("hb") != state["hb"]:
                findings.append(f"PROGRESS_DELTA · {tid} · heartbeat · {titles.get(tid, tid)}")
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
            if age_m >= QUEUE_AGING_MINUTES:
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

        if status == "review":
            last = conn.execute(
                "SELECT MAX(id) AS m FROM task_runs WHERE task_id=?", (tid,)).fetchone()
            if not last or not last["m"]:
                age_h = (now - (t["started_at"] or t["created_at"] or now)) / 3600
                if age_h >= 2:
                    findings.append(
                        f"REVIEW_STALLED · {tid} · review requested {age_h:.0f}h ago, no reviewer dispatched · {title}")
            else:
                row = conn.execute(
                    "SELECT ended_at FROM task_runs WHERE id=?", (last["m"],)).fetchone()
                ended = row["ended_at"] if row and row["ended_at"] else now
                age_h = (now - ended) / 3600
                if age_h >= 2:
                    findings.append(
                        f"REVIEW_STALLED · {tid} · review requested {age_h:.0f}h ago, no reviewer dispatched · {title}")

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

    conn.close()

    busy = any(t["status"] in ("ready", "running", "in_progress", "review") for t in tasks)
    if not busy:
        orphans = sum(1 for f in findings if f.startswith("ORPHANED_CHAIN"))
        awaiting = sum(1 for f in findings if f.startswith("COORDINATOR_WAKE"))
        if orphans:
            classification = "done-with-orphans"
        elif awaiting:
            classification = f"awaiting decisions ({awaiting} blocked)"
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
