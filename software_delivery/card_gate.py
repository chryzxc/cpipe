"""``pre_tool_call`` gate: delivery cards come only from ``delivery_submit`` and its fix/verify tools.

A hand-made card skips the chain's brief (plan, same-card review, verify) and turns each finding into
a new card with a cold worker: one two-task day became 38 cards. Only the delivery roles are guarded;
the user can still create any card from their own terminal.
"""

from __future__ import annotations

import re

from . import submit

ROLES = ("implementer", "reviewer", "verifier", "planner", "investigator", "release_engineer",
         "security_reviewer", "security_tester")
CREATE_CMD = re.compile(r"\bkanban\s+create\b.*?--assignee[=\s]+['\"]?([\w-]+)", re.S)


def gate(tool_name: str = "", args: dict | None = None, **_kw):
    args = args or {}
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
