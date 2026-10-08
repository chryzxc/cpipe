"""Native, frozen-revision consent for publishing a draft or marking it ready."""
from __future__ import annotations

import hashlib
import json
from contextlib import closing

from . import plan_approval, report, tools
from .runtime import hermes_api as h

NAME = "cpipe_approve_action"
SCHEMA = {"name": NAME, "description": "Open native approval for this exact delivery's publication action; never merge or deploy.",
          "parameters": {"type": "object", "required": ["task_id", "approval_key"],
                         "properties": {"task_id": {"type": "string"}, "approval_key": {"type": "string"}}}}


def snapshot(conn, task, sid):
    if (not sid or not task or task.session_id != sid or not h.is_delivery(task)
            or task.status not in ("review", "blocked")):
        return None
    state = report.state_of(conn, task)
    if state.get("node") == "verified":
        action = "Publish draft PR"
    elif state.get("node") == "handoff" and state.get("pr_url"):
        action = "Mark PR ready"
    else:
        return None
    payload = {"task_id": task.id, "session_id": sid, "action": action, "state": state}
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return key, action, state


def offer(conn, task, sid):
    snap = snapshot(conn, task, sid)
    if not snap:
        return None
    key, action, _ = snap
    with plan_approval._store() as store:
        store.execute("CREATE TABLE IF NOT EXISTS action_approvals (session_id TEXT, task_id TEXT, approval_key TEXT, status TEXT, PRIMARY KEY(session_id,task_id,approval_key))")
        row = store.execute("SELECT status FROM action_approvals WHERE session_id=? AND task_id=? AND approval_key=?",
                            (sid, task.id, key)).fetchone()
    if row:
        return {"context": f"Native publication approval was already handled ({row[0]}). Leave the delivery waiting; do not re-prompt or infer consent. Report status."}
    return {"context": f"Delivery {task.id} is waiting: {action}. Open Hermes' native approve/reject UI "
            f"with {NAME} using these exact arguments: "
            + json.dumps({"task_id": task.id, "approval_key": key})
            + ". Do not ask for a chat reply like proceed. This does not authorize merge or deployment. "
            "If denied, dismissed, stale or unavailable, leave it waiting and report the result."}


def approve(args, **_):
    from .native import _session
    sid = _session(None)
    with closing(h.connect(None)) as conn:
        task = h.get_task(conn, args.get("task_id", ""))
        snap = snapshot(conn, task, sid)
        if not snap or snap[0] != args.get("approval_key"):
            return json.dumps({"approved": False, "error": "missing, stale or wrong-session approval"})
        key, action, state = snap
        with plan_approval._store() as store:
            store.execute("CREATE TABLE IF NOT EXISTS action_approvals (session_id TEXT, task_id TEXT, approval_key TEXT, status TEXT, PRIMARY KEY(session_id,task_id,approval_key))")
            if store.execute("SELECT 1 FROM action_approvals WHERE session_id=? AND task_id=? AND approval_key=?",
                             (sid, task.id, key)).fetchone():
                return json.dumps({"approved": False, "error": "already-handled approval; use status"})
            store.execute("INSERT INTO action_approvals VALUES (?, ?, ?, 'requested')", (sid, task.id, key))
    payload = {"action": action, "title": task.title, "pr_url": state.get("pr_url"),
               "verification": state.get("summary"), "approved_commit": state.get("verified_head"),
               "authority": "Only this publication action; no merge, deployment or credential changes"}
    command = action + "\n" + json.dumps(payload, sort_keys=True, ensure_ascii=False)
    try:
        decision = plan_approval._native_prompt(command, json.dumps(payload, ensure_ascii=False),
                                               "cpipe_publication_approval", action + "?")
    except Exception:
        decision = {"unavailable": True}
    approved = (decision.get("resolved") and decision.get("choice") == "once"
                and not any(decision.get(k) for k in ("cancelled", "notify_failed", "unavailable")))
    reason = "denied" if decision.get("choice") == "deny" else "no approval received"
    with closing(h.connect(None)) as conn:
        current = snapshot(conn, h.get_task(conn, task.id), sid)
        approved = approved and bool(current and current[0] == key)
        with plan_approval._store() as store:
            store.execute("UPDATE action_approvals SET status=? WHERE session_id=? AND task_id=? AND approval_key=?",
                          ("approved" if approved else reason, sid, task.id, key))
        if not approved:
            return json.dumps({"task_id": task.id, "approved": False, "reason": reason, "status": "waiting",
                               "note": "Delivery remains waiting; no publication queued"})
    result = tools.stage("ship", {"task_id": task.id, "ready": action == "Mark PR ready"})
    return json.dumps({**result, "approved": True, "action": action})


def register(ctx):
    if hasattr(ctx, "register_tool"):
        def handler(args, **kwargs):
            try:
                return approve(args, **kwargs)
            except Exception as exc:
                return json.dumps({"error": f"Native publication approval failed: {type(exc).__name__}; inspect status before retrying"})
        ctx.register_tool(name=NAME, toolset="cpipe", schema=SCHEMA, handler=handler)
