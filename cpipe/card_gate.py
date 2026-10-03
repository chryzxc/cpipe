"""``pre_tool_call`` gate: delivery cards come only from ``delivery_submit`` and its fix/verify tools.

A hand-made card skips the chain's brief (plan, same-card review, verify) and turns each finding into
a new card with a cold worker: one two-task day became 38 cards. Only the delivery roles are guarded;
the user can still create any card from their own terminal.
"""

from __future__ import annotations

import json
import os
import re
import shlex
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
LOOKUP_TOOLS = ("read_file", "search_files", "terminal")
_lookups: dict[str, int] = {}  # Plan: card -> lookups so far (per worker process)
GH_WRITE = re.compile(r"\bgh\s+(?:(?:pr|issue)\s+(create|edit|comment|review)|api\b.*/(comments|reviews)\b)", re.S)
BODY_FLAGS = ("--body", "-b", "--title", "-t")
FILE_FLAGS = ("--body-file", "-F")
FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field")
# What leaked into public PRs: local paths, card ids, delivery tokens, plan and review ids, gate names, SHAs.
# ponytail: tool names (Hermes, cpipe) are left out: they are real content in repos about them.
LEAK = re.compile(
    r"/Users/|/home/\w+/|~/\.hermes|\.hermes/|\.worktrees/|\bt_[0-9a-f]{8}\b"
    r"|\b(?:READY_WITH_RISK|COMMIT_READY|OUT_OF_PLAN|PINNED|CONTINUATION|REQUEST_CHANGES|PRECHECK)\b"
    r"|\bREV-\d+\b|\bAC\d+\b|\bOCR (?:gate|delegat|preview|review|belongs)"
    r"|(?<!/commit/)(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])",
    re.I)


def gate(tool_name: str = "", args: dict | None = None, **_kw):
    args = args or {}
    blocked = (round_limit_unblock(tool_name, args) or operator_config_write(tool_name, args)
               or plan_lookup_budget(tool_name, args) or public_github_text(tool_name, args))
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


def plan_lookup_budget(tool_name: str, args: dict):
    """The planner plans from the investigator's map. Exploring the repo itself took 40-74 lookups a plan
    on the most expensive model; the brief allows submit.PLAN_LOOKUPS to close named GAPs."""
    tid = os.environ.get("HERMES_KANBAN_TASK", "")
    if tool_name not in LOOKUP_TOOLS or not tid or "hermes kanban" in str(args.get("command") or ""):
        return None
    if Path(os.environ.get("HERMES_HOME", "")).name != _planner():
        return None
    if tid not in _lookups:
        row = submit._task_row(tid)
        if not (row and str(row["title"]).startswith("Plan:")):
            return None
        _lookups[tid] = 0
    _lookups[tid] += 1
    if _lookups[tid] <= submit.PLAN_LOOKUPS:
        return None
    return {"action": "block", "message": (
        f"Lookup budget spent ({submit.PLAN_LOOKUPS}): plan from the map you have. Put each fact still open "
        "under VERIFY for the implementer and complete the card with the full plan.")}


def _planner() -> str:
    try:
        return submit._role("planner")
    except (KeyError, OSError):
        return ""


def public_github_text(tool_name: str, args: dict):
    """PR titles, bodies and comments are public. The rule was prose plus an optional linter: workers ran it
    on 19 of 80 PR writes and leaked local paths, card ids, bot names and review rounds into PRs."""
    cmd = str(args.get("command") or "") if tool_name == "terminal" else ""
    m = GH_WRITE.search(cmd)
    if not m:
        return None
    worker = Path(os.environ.get("HERMES_HOME", ""))
    if m[1] in ("comment", "review") or m[2]:
        if worker.parent.name == "profiles" and worker.name in guarded():
            return {"action": "block", "message": (
                "Not posted: delivery workers never comment on or review the PR on GitHub. Your findings and "
                "evidence go in your card report; the PR body is the only public text you write.")}
    text = _gh_text(cmd[m.start():])
    hit = LEAK.search(text) or _bot_name(text)
    if not hit:
        return None
    return {"action": "block", "message": (
        f"Not sent: the PR text contains {hit[0]!r}, which is internal. The PR is public: describe the change, "
        "how to test it and real risks; never local paths, card/plan/review ids, bot or tool names, models, "
        "SHAs, delivery tokens, or review rounds. Rewrite it and send again.")}


def _gh_text(cmd: str) -> str:
    """Titles and bodies a gh command would publish, from inline flags, body files and api fields."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return cmd
    out = []
    for flag, value in zip(tokens, tokens[1:]):
        if flag in FIELD_FLAGS and value.startswith("body="):
            value = value[5:]
            flag = "--body-file" if value.startswith("@") else "--body"
            value = value.lstrip("@")
        if flag in BODY_FLAGS:
            out.append(value)
        elif flag in FILE_FLAGS and value != "-":
            try:
                out.append(Path(value).expanduser().read_text(errors="replace"))
            except OSError:
                pass
    return "\n".join(out)


def _bot_name(text: str):
    names = "|".join(re.escape(n) for n in guarded() | {_coordinator()} if n)
    return names and re.search(rf"\b(?:{names})\b\s*(?:/|review|QA|verif|approv|gate|pass|fail)", text, re.I)


def _coordinator() -> str:
    try:
        return submit._role("coordinator")
    except (KeyError, OSError):
        return ""
