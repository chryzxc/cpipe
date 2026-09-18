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
import sqlite3
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DB = HERMES_HOME / "kanban.db"
GLOBAL_SKILLS = HERMES_HOME / "skills"
PROFILES = HERMES_HOME / "profiles"
TODO_AGING_HOURS = 24
QUEUE_AGING_MINUTES = 30
COORDINATOR_WAKE_MINUTES = 15
HEARTBEAT_STALE_MINUTES = 5
NEXUS_ACTION_KEYWORDS = ("plan_amendment", "needs_assistance", "re-specif",
                         "respecify", "nexus triage", "blocked: plan",
                         "blocked: needs")

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


def main() -> None:
    if not DB.exists():
        print("NO BOARD")
        return
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = time.time()
    findings: list[str] = []

    tasks = conn.execute(
        "SELECT id, title, status, assignee, skills, created_at, started_at, "
        "last_heartbeat_at FROM tasks "
        "WHERE status IN ('blocked','todo','review','running','in_progress')"
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

    for t in tasks:
        tid, title = t["id"], (t["title"] or "")[:60]
        assignee, status = t["assignee"], t["status"]

        if status == "todo" and not assignee:
            age_h = (now - (t["created_at"] or now)) / 3600
            if age_h >= TODO_AGING_HOURS:
                findings.append(f"UNASSIGNED_TODO · {tid} · aging {age_h:.0f}h · {title}")
            continue

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
            if last and last["m"]:
                row = conn.execute(
                    "SELECT ended_at FROM task_runs WHERE id=?", (last["m"],)).fetchone()
                ended = row["ended_at"] if row and row["ended_at"] else now
                age_h = (now - ended) / 3600
                if age_h >= 2:
                    findings.append(
                        f"REVIEW_STALLED · {tid} · review requested {age_h:.0f}h ago, no reviewer dispatched · {title}")

        if status in ("running", "in_progress"):
            hb = t["last_heartbeat_at"]
            if hb and (now - hb) / 60 >= HEARTBEAT_STALE_MINUTES:
                findings.append(
                    f"HEARTBEAT_STALE · {tid} · no heartbeat {((now-hb)/60):.0f}m · {title}")
            row = conn.execute(
                "SELECT claim_expires FROM task_runs WHERE task_id=? "
                "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
            expires = row["claim_expires"] if row and row["claim_expires"] else None
            if expires and expires < now:
                findings.append(f"STALE_CLAIM · {tid} · claim expired {((now-expires)/60):.0f}m ago · {title}")

    conn.close()

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
