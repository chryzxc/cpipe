"""``pre_tool_call`` route for ``clarify`` in a headless turn.

With no app holding Bot Chat open, cron output reaches the coordinator as a ``hermes chat -Q`` turn,
and there ``clarify`` answers itself ("no user available… pick the best option"), so the model
guesses the decisions it meant to ask about. This hook stops that: the question is saved to
``$HERMES_HOME/delivery/questions/<card>.json`` and the card is blocked. The block notice reaches
the chat subscribed to the card, and Herm reads the file to show the choices as buttons. The
answer comes back as ``ANSWER <card>: <choice>``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time

from . import submit

HEADLESS_ENV = "HERMES_QUIET_TURN_REPORT_FILE"  # set only on the cron -> Bot Chat -Q lane
CARD = re.compile(r"\bt_[0-9a-f]{8}\b")


def questions_dir():
    return submit.HERMES_HOME / "delivery" / "questions"


def gate(tool_name: str = "", args: dict | None = None, session_id: str = "", **_kw):
    if tool_name != "clarify" or not os.environ.get(HEADLESS_ENV) or os.environ.get("HERMES_KANBAN_TASK"):
        return None
    args = args or {}
    items = args.get("questions") or [{"question": args.get("question", ""), "choices": args.get("choices"),
                                       "multi_select": args.get("multi_select", False)}]
    items = [i if isinstance(i, dict) else {"question": str(i)} for i in items]
    cards = CARD.findall(json.dumps(items))
    if not cards:
        return _block("Name the card this decides (its t_… id) in the question and call clarify again. "
                      "If it is not about a card, put the question in your final reply and stop.")
    tid = cards[0]
    try:
        conn = sqlite3.connect(f"file:{submit._db()}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()
        finally:
            conn.close()
        if row and row[0] == "running":
            return _block(f"{tid} is still running. Do not decide for it: wait for its result, then ask.")
        questions_dir().mkdir(parents=True, exist_ok=True)
        (questions_dir() / f"{tid}.json").write_text(json.dumps({
            "task_id": tid, "session_id": session_id, "profile": _profile(), "asked_at": int(time.time()),
            "questions": [{"question": str(i.get("question") or ""), "choices": list(i.get("choices") or []),
                           "multi_select": bool(i.get("multi_select"))} for i in items]}, indent=1))
        first = str(items[0].get("question") or "")
        blocked = submit._hermes("kanban", "block", "--kind", "needs_input", tid, f"QUESTION: {first}")
        if blocked.returncode != 0:  # a todo card cannot block; the comment still records the question
            submit._hermes("kanban", "comment", tid, f"QUESTION for the user: {first}")
        submit._journal("clarify.queued", tid, session=session_id)
    except Exception as exc:  # the question must not silently vanish into a guess
        return _block(f"Could not queue the question ({type(exc).__name__}). Put it in your final reply "
                      "and stop without choosing.")
    state = "is blocked with" if blocked.returncode == 0 else "has a comment with"
    return _block(f"Queued for the user: {tid} {state} this question and Herm shows the choices. "
                  f"Do NOT pick an option or act on it. End the turn with one line saying {tid} waits on "
                  f"the user. The answer arrives later as `ANSWER {tid}: <choice>`.")


def _profile() -> str:
    home = submit.HERMES_HOME
    return home.name if home.parent.name == "profiles" else "default"


def _block(why: str) -> dict:
    return {"action": "block", "message": f"No user is attached to this turn, so clarify cannot be answered. {why}"}
