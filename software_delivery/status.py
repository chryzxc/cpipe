"""The board's truth, handed to the chat: ``delivery_status`` / ``delivery_watch`` tools and the
chat-side hooks that keep a coordinator from reporting progress the board does not show.

- ``delivery_status``: the monitor's verdict per card (PROGRESSING / WAITING(<named>) / STUCK(<cause>)),
  read-only, computed from ``kanban.db`` now — not from what a worker last said.
- ``delivery_watch``: register a PR/CI the monitor polls; a card "will resume" only with one.
- ``pre_llm_call``: deliver the monitor's pending notices for this chat, a correction when the
  previous reply claimed progress the board contradicts, and journal pure progress nudges.
- ``post_llm_call``: check the reply's progress claims against the verdicts.
- ``pre_approval_request``: journal approval prompts (a worker waiting on one is not "running").
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

_SCRIPTS = Path(__file__).resolve().parents[1] / "workflow" / "scripts"
_modules: dict = {}

NUDGE_RE = re.compile(r"^\s*(any\s+(update|progress|news)|status\??|update\??|progress\??|is it (done|finished)|"
                      r"how('?s| is) it going|still (working|running)|what'?s the status|eta\??)[\s?.!]*$", re.I)
CLAIM_RE = re.compile(r"\b(will (resume|continue|pick (it|this) up)|is (running|progressing|in progress|moving)|"
                      r"(still|currently) (working|running)|on track|should (finish|complete) (soon|shortly))\b", re.I)
CARD_RE = re.compile(r"\bt_[0-9a-f]{6,}\b")
_corrections: dict[str, str] = {}
_CORRECTIONS_MAX = 256


def _load(name: str):
    if name not in _modules:
        import sys
        sys.path.insert(0, str(_SCRIPTS))  # delivery_monitor imports its siblings
        spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault(name, module)  # dataclasses resolve annotations through sys.modules
        spec.loader.exec_module(module)
        _modules[name] = sys.modules[name]
    return _modules[name]


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _journal(kind: str, card: Optional[str] = None, **detail) -> None:
    try:
        _load("delivery_journal").record(kind, card, home=_home(), **detail)
    except Exception:
        pass


def _chat_id() -> str:
    try:
        from gateway.session_context import get_session_env
        chat = get_session_env("HERMES_SESSION_CHAT_ID", "")
    except Exception:
        chat = ""
    return chat or os.environ.get("HERMES_SESSION_CHAT_ID", "")


# ---------------------------------------------------------------- tools

def status(card: Optional[str] = None, home: Optional[Path] = None) -> dict:
    home = home or _home()
    verdicts, b = _load("delivery_monitor").evaluate(home)
    rows = []
    for tid, v in verdicts.items():
        if card and card not in (tid, v.chain):
            continue
        t = b["tasks"][tid]
        rows.append({"card": tid, "title": t["title"], "status": t["status"], "assignee": t["assignee"],
                     "verdict": v.label, "evidence": v.evidence, "chain": v.chain,
                     "waiting_on_it": v.blocked_children or None,
                     "next": _load("delivery_monitor").NEXT.get(v.cause) if v.state == "STUCK" else None})
    rows.sort(key=lambda r: ({"STUCK": 0, "WAITING": 1}.get(r["verdict"].split("(")[0], 2), r["card"]))
    counts: dict = {}
    for r in rows:
        key = r["verdict"].split("(")[0]
        counts[key] = counts.get(key, 0) + 1
    if card and not rows:
        return {"ok": False, "error": f"{card} is not an open card (done, archived, or unknown)"}
    return {"ok": True, "counts": counts, "cards": rows}


def delivery_status(args: dict, **_: Any) -> str:
    try:
        return json.dumps(status(args.get("card")), default=str)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"board unreadable: {exc}"})


def watch(card: str, kind: str, ref: str, hours: float = 24, home: Optional[Path] = None) -> dict:
    if kind not in ("pr", "ci"):
        return {"ok": False, "error": "kind must be pr or ci"}
    if not CARD_RE.fullmatch(card or ""):
        return {"ok": False, "error": "card must be a kanban task id (t_…)"}
    if not re.match(r"^(https://github\.com/[\w.-]+/[\w.-]+/pull/\d+|\d+)$", ref or ""):
        return {"ok": False, "error": "ref must be a GitHub PR URL or number"}
    mon = _load("delivery_monitor")
    path = (home or _home()) / "state" / "delivery-watches.json"
    watches = mon._load(path)
    watches[card] = {"kind": kind, "ref": ref, "until": time.time() + float(hours) * 3600, "at": time.time()}
    mon._save(path, watches)
    _journal("watch.registered", card, watch=kind, ref=ref, hours=hours)
    return {"ok": True, "watching": watches[card],
            "note": "the delivery monitor polls this every 5 minutes; pass → unblock, fail/expiry → escalation"}


def delivery_watch(args: dict, **_: Any) -> str:
    return json.dumps(watch(args.get("card", ""), args.get("kind", ""), str(args.get("ref", "")),
                            args.get("hours") or 24))


STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delivery_status",
        "description": ("The board's truth for delivery work: each open card's verdict computed from kanban.db "
                        "now — PROGRESSING (live worker), WAITING(<named thing>), or STUCK(<cause>) with the next "
                        "step. Call this before answering ANY status question; never report progress from memory "
                        "or from a worker's last message."),
        "parameters": {"type": "object", "properties": {
            "card": {"type": "string", "description": "Optional card id or chain root id; omit for the whole board"}},
            "required": []},
    },
}

WATCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delivery_watch",
        "description": ("Register a PR or CI run the delivery monitor polls for a card. Required before telling "
                        "anyone a card 'will resume' after a PR merges or CI passes: pass → the card is unblocked, "
                        "failure or expiry → the chat is told."),
        "parameters": {"type": "object", "properties": {
            "card": {"type": "string", "description": "Card id (t_…) waiting on the PR/CI"},
            "kind": {"type": "string", "enum": ["pr", "ci"], "description": "pr: wait for merge; ci: wait for checks"},
            "ref": {"type": "string", "description": "GitHub PR URL or number"},
            "hours": {"type": "number", "description": "Give up and escalate after this many hours (default 24)"}},
            "required": ["card", "kind", "ref"]},
    },
}


# ---------------------------------------------------------------- hooks

def take_notices(chat: str, home: Optional[Path] = None) -> list[dict]:
    if not chat:
        return []
    mon = _load("delivery_monitor")
    path = (home or _home()) / "state" / "delivery-notices.json"
    pending = mon._load(path)
    mine = pending.pop(chat, [])
    if mine:
        # ponytail: read-modify-write without a lock; a notice the monitor adds in the same instant
        # can be lost (it re-escalates on its next state change). flock if that ever matters.
        mon._save(path, pending)
    return mine


def chat_context(*, session_id: Optional[str] = None, user_message: Any = None, home: Optional[Path] = None,
                 **_: Any) -> Optional[dict]:
    """``pre_llm_call``: monitor notices for this chat + the pending claim correction; journal nudges."""
    try:
        parts = []
        correction = _corrections.pop(session_id or "", None)
        if correction:
            parts.append(correction)
        for n in take_notices(_chat_id(), home):
            parts.append(f"{n.get('card')} ({n.get('title')}): {n.get('text')}")
        if isinstance(user_message, str) and NUDGE_RE.match(user_message):
            _journal("nudge", None, session=session_id, text=user_message[:80])
            parts.append("The operator asked for progress: call delivery_status and answer from its verdicts only.")
        if not parts:
            return None
        return {"context": "[software-delivery monitor] " + "\n".join(parts)}
    except Exception:
        return None


def claim_check(*, session_id: Optional[str] = None, assistant_response: Any = None,
                home: Optional[Path] = None, **_: Any) -> None:
    """``post_llm_call``: a reply that says a card is moving must match the board."""
    try:
        text = assistant_response if isinstance(assistant_response, str) else ""
        cards = set(CARD_RE.findall(text))
        if not cards or not CLAIM_RE.search(text):
            return
        verdicts, _ = _load("delivery_monitor").evaluate(home or _home())
        wrong = []
        for tid in sorted(cards):
            v = verdicts.get(tid)
            if v is None or v.state == "STUCK" or (v.state == "WAITING" and v.cause in ("NEEDS_HUMAN", "TRIAGE_PARKED")):
                wrong.append(f"{tid} is {v.label if v else 'not open'}")
                _journal("claim.unsupported", tid, session=session_id, verdict=v.label if v else "closed",
                         claim=CLAIM_RE.search(text).group(0))
        if wrong and session_id:
            if len(_corrections) >= _CORRECTIONS_MAX:
                _corrections.clear()
            _corrections[session_id] = ("Your last reply said work is moving, but the board says: " + "; ".join(wrong)
                                        + ". Correct it in one line to the operator using delivery_status.")
    except Exception:
        pass


def approval_requested(*, command: str = "", surface: str = "", session_key: str = "", run=None, **_: Any) -> None:
    """``pre_approval_request``: a worker waiting on a person is not progressing. In a kanban worker nobody
    sees the prompt, so the card is blocked with ``APPROVAL_NEEDED`` and the escalation reaches the chat."""
    card = os.environ.get("HERMES_KANBAN_TASK") or None
    _journal("approval.requested", card, command=(command or "")[:120], surface=surface, session=session_key)
    if not card:
        return
    reason = (f"APPROVAL_NEEDED: {(command or '?')[:200]}; next: allow it in the worker profile's "
              "approvals config, or re-specify the card without it, then unblock")
    try:
        (run or _load("delivery_monitor").hermes)("kanban", "block", card, "--kind", "needs_input", reason)
    except Exception:
        pass
