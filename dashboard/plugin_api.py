"""Mission Control backend: the stuck-card queue and the operator's actions on it.

Groups come from workflow/scripts/board_triage.py (same as the daily chat reminder). Moves go through
the `hermes kanban` CLI or kanban_db, so every board rule (parent gating, audit events) still applies.
"""

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
_spec = importlib.util.spec_from_file_location("board_triage", REPO / "workflow" / "scripts" / "board_triage.py")
triage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(triage)

router = APIRouter()


def _read():
    conn = sqlite3.connect(f"file:{triage.DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/queue")
def queue():
    conn = _read()
    try:
        return triage.queue(conn)
    finally:
        conn.close()


@router.get("/cards/{task_id}")
def card(task_id: str):
    conn = _read()
    try:
        row = conn.execute("SELECT id, title, body, status, assignee, project_id, workspace_path, result "
                           "FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"{task_id} not found")
        comments = conn.execute("SELECT author, body, created_at FROM task_comments WHERE task_id=? "
                                "ORDER BY id DESC LIMIT 5", (task_id,)).fetchall()
        reason = triage._reason(conn, task_id)
    finally:
        conn.close()
    brief = f"{row['title']}\n\n{row['body'] or ''}".strip()[: 2800]
    return {**dict(row), "reason": reason, "comments": [dict(c) for c in comments],
            "suggested": {"title": row["title"], "request": brief, "project": row["project_id"] or ""}}


class Act(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    action: Literal["continue", "archive"]
    note: str = ""


def _continue(tid: str, status: str, note: str):
    if status in ("blocked", "scheduled"):
        return triage.hermes("kanban", "unblock", tid, *(["--reason", note] if note else []))
    if note:
        triage.hermes("kanban", "comment", tid, note, "--author", "operator")
    if status == "triage":  # no CLI verb moves triage -> todo without an LLM rewrite; this is the same call
        from hermes_cli import kanban_db
        from hermes_cli.kanban_db_connect import connect_closing
        with connect_closing() as conn:
            moved = kanban_db.specify_triage_task(conn, tid, author="operator")
        return moved, "moved to todo" if moved else "not in triage"
    if status == "todo":
        return triage.hermes("kanban", "promote", tid, note or "operator: continue")
    return False, f"card is {status}; nothing to continue"


@router.post("/act")
def act(body: Act):
    conn = _read()
    try:
        status = dict(conn.execute(f"SELECT id, status FROM tasks WHERE id IN ({','.join('?' * len(body.ids))})",
                                   body.ids).fetchall())
    finally:
        conn.close()
    results = {}
    for tid in body.ids:
        if tid not in status:
            results[tid] = {"ok": False, "detail": "not found"}
            continue
        if body.action == "continue":
            ok, detail = _continue(tid, status[tid], body.note.strip())
        else:
            if body.note.strip():
                triage.hermes("kanban", "comment", tid, body.note.strip(), "--author", "operator")
            ok, detail = triage.hermes("kanban", "archive", tid)
        results[tid] = {"ok": ok, "detail": detail[-300:]}
    return {"results": results}


class Resubmit(BaseModel):
    title: str
    request: str
    project: str
    size: Literal["small", "large"] = "small"


@router.post("/cards/{task_id}/resubmit")
def resubmit(task_id: str, body: Resubmit):
    """Start the work fresh under the current flow (plan -> pin -> build -> review -> verify)."""
    from software_delivery import submit
    out = json.loads(submit.submit(body.model_dump()))
    if not out.get("ok"):
        return out
    for new_id in out["cards"].values():
        submit._copy_subscriptions(task_id, new_id)  # whoever followed the old card hears about the new one
    triage.hermes("kanban", "comment", task_id, f"Resubmitted as {out['cards']}", "--author", "operator")
    out["archived_old"] = triage.hermes("kanban", "archive", task_id)[0]
    return out
