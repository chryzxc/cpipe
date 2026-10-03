"""``pre_tool_call`` gate: delivery cards come only from ``delivery_submit`` and its fix/verify tools.

A hand-made card skips the chain's brief (plan, same-card review, verify) and turns each finding into
a new card with a cold worker: one two-task day became 38 cards. Only the delivery roles are guarded;
the user can still create any card from their own terminal.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from . import home, submit

ROLES = ("implementer", "reviewer", "verifier", "planner", "investigator", "release_engineer",
         "security_reviewer", "security_tester")
UNBLOCK_CMD = re.compile(r"\bkanban\s+unblock\s+['\"]?(t_[0-9a-f]+)")
ROUND_LIMIT = re.compile(r"round limit", re.I)
CREATE_CMD = re.compile(r"\bkanban\s+create\b.*?--assignee[=\s]+['\"]?([\w-]+)", re.S)
OPERATOR_DIRS = ("delivery", "memories", "skills", "plugins", "scripts", "cron")
PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)


def gate(tool_name: str = "", args: dict | None = None, **_kw):
    args = args or {}
    blocked = round_limit_unblock(tool_name, args) or operator_config_write(tool_name, args)
    if blocked:
        return blocked
    if tool_name == "kanban_create":
        assignee = str(args.get("assignee") or "")
    elif tool_name == "terminal" and (m := CREATE_CMD.search(str(args.get("command") or ""))):
        assignee = m[1]
    else:
        return None
    if assignee not in guarded():
        return None
    return {"action": "block", "message": (
        f"No card was created: {assignee} cards come only from the delivery tools. New work: `delivery_submit`. "
        "A problem the user found in a PR: `delivery_submit` with `fix_of`. A reviewer finding stays on its card "
        "(request-changes); a verifier failure goes through `delivery_verify_failed`. A scope change: comment on "
        "the running card, or archive its open cards and resubmit. Never split one task into more cards.")}


def guarded() -> set[str]:
    profiles = set()
    for role in ROLES:
        try:
            profiles.add(submit._role(role))
        except (KeyError, OSError):  # unmapped role, or no roster at all
            pass
    return profiles


def round_limit_unblock(tool_name: str, args: dict):
    """A card blocked at the fix-round limit waits for the user: a chat unblocking it re-runs the loop
    the limit stopped. The user unblocks it from their own terminal."""
    if tool_name == "kanban_unblock":
        card = str(args.get("task_id") or "")
    elif tool_name == "terminal" and (m := UNBLOCK_CMD.search(str(args.get("command") or ""))):
        card = m[1]
    else:
        return None
    if not ROUND_LIMIT.search(last_block_reason(card)):
        return None
    return {"action": "block", "message": (
        f"{card} stopped at the fix-round limit, so the decision is Christian's. Do not unblock it: give him the "
        f"remaining failures and 2-3 options (accept as is, one more fix round, or drop it). If he chooses to go "
        f"on, he runs `hermes kanban unblock {card}` himself.")}


def last_block_reason(card: str) -> str:
    try:
        conn = sqlite3.connect(f"file:{submit._db()}?mode=ro", uri=True)
        row = conn.execute("SELECT payload FROM task_events WHERE task_id = ? AND kind = 'blocked' "
                           "ORDER BY id DESC LIMIT 1", (card,)).fetchone()
        conn.close()
        return str((json.loads(row[0]) or {}).get("reason") or "") if row and row[0] else ""
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return ""


def operator_config_write(tool_name: str, args: dict):
    """A worker that edits the operator's setup (launch recipes, SOUL, config, skills) changes every later
    card: one fix round swapped the project's test server for a stub. Workers report the problem instead."""
    worker = Path(os.environ.get("HERMES_HOME", ""))
    if tool_name not in ("patch", "write_file") or worker.parent.name != "profiles" or worker.name not in guarded():
        return None
    paths = [args.get("path")] + PATCH_FILE.findall(str(args.get("patch") or ""))
    hit = next((p for p in paths if p and operator_path(Path(str(p)).expanduser())), None)
    if not hit:
        return None
    return {"action": "block", "message": (
        f"Not written: {hit} is the operator's Hermes setup, which workers never edit. If a launch recipe, "
        "config or skill is wrong, say what fails and what the fix is in your report; the user changes it.")}


def operator_path(path: Path) -> bool:
    root = home.root().resolve()
    try:
        rel = path.resolve().relative_to(root).parts
    except ValueError:
        return False
    return (len(rel) == 1 or rel[0] in OPERATOR_DIRS
            or (len(rel) == 3 and rel[0] == "profiles" and rel[2] in ("SOUL.md", "config.yaml")))
