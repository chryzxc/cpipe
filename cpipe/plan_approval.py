"""Single-plan, same-session human consent via Hermes' native approval surface.

There is no model-supplied consent boolean. The transport's one-operation answer
is revalidated against the stored plan before using the existing build directive.
Hermes internal transport imports are isolated here and fail closed on upgrades.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import closing, contextmanager

from . import native, report
from .runtime import hermes_api as h

NAME = "cpipe_approve_plan"
SCHEMA = {
    "name": NAME,
    "description": "Show Hermes' native approval UI for an existing, completed cpipe plan in this session. "
                   "Call with the exact task_id and plan_revision from cpipe context when presenting the plan. "
                   "Only the user's one-operation accept resumes building; rejection leaves it waiting. "
                   "Never starts a new delivery or authorizes PR readiness, merging or deployment.",
    "parameters": {"type": "object", "additionalProperties": False,
                   "properties": {"task_id": {"type": "string"}, "plan_revision": {"type": "string"}},
                   "required": ["task_id", "plan_revision"]},
}


@contextmanager
def _store():
    with native._store() as store:
        store.execute("""CREATE TABLE IF NOT EXISTS plan_approvals (
            session_id TEXT NOT NULL, task_id TEXT NOT NULL, revision TEXT NOT NULL,
            result TEXT NOT NULL, PRIMARY KEY(session_id, task_id, revision))""")
        yield store


def _snapshot(conn, task, sid):
    snap = native._snapshot(conn, task, sid)
    if not snap:
        return None
    state = report.state_of(conn, task)
    if (state.get("node") != "proposal" or state.get("approved") or state.get("questions")
            or not isinstance(state.get("plan"), str) or not state["plan"].strip()):
        return None
    comments = h.comments_after(conn, task.id)
    # A queued explicit build/ship must not offer a second approval before its worker starts.
    if any(c.author in ("cpipe:approved", "cpipe:ready", "cpipe:stage") for c in comments):
        return None
    signature = json.dumps([sid, task.id, task.title, task.body, task.assignee,
                            task.workspace_path, snap[0], comments[-1].id if comments else None])
    return hashlib.sha256(signature.encode()).hexdigest(), state


def offer(conn, task, sid):
    snap = _snapshot(conn, task, sid)
    if not snap:
        return None
    revision, _ = snap
    with _store() as store:
        row = store.execute("SELECT result FROM plan_approvals WHERE session_id=? AND task_id=? AND revision=?",
                            (sid, task.id, revision)).fetchone()
    if row:
        result = json.loads(row["result"])
        if result["status"] != "approval_pending":
            return None  # native clarification can collect changes; never re-offer the same rejected plan
        return {"context": f"cpipe plan approval: {result['status']}. Leave this delivery waiting unless "
                "the user explicitly requests changes or /cp-build. Do not re-prompt or infer consent."}
    args = {"task_id": task.id, "plan_revision": revision}
    return {"context": f"cpipe delivery {task.id} has a completed plan awaiting human approval. Only when "
            f"presenting or discussing this plan, call {NAME} with the exact arguments below to show Hermes' "
            "native accept/reject UI. Do not use clarify as an approval substitute or ask the user to type a command. "
            "For unrelated conversation ignore this context. PR readiness, merging and deployment remain separate.\n"
            "Native approval arguments:\n" + json.dumps(args)}


def _prompt(task, state, revision):
    """Use the same one-operation transport as Hermes' protected-file consent gate.

    Unlike routine command safety, this must not honor --yolo, cached scopes,
    auto-approve policy or unattended workers. No session/permanent buttons.
    """
    command = f"Build approved plan: {task.title} [{revision[:12]}]"
    description = ("Accept to authorize the existing build workflow for this exact plan: implementation, tests and "
                   "configured review, including a draft PR if enabled for this delivery. Reject leaves the plan "
                   "waiting for changes. PR readiness, merging and deployment are not authorized.\n\n" + state["plan"])
    if state.get("parts"):
        description += "\n\nSeparate changes:\n" + "\n".join(
            str(part.get("title", "")) for part in state["parts"])
    if state.get("routing"):
        from .routing import describe
        description += "\n\nConfirmed execution route:\n" + describe(state["routing"])
    return _native_prompt(command, description, "cpipe_plan_approval", "Approve this development plan?")


def _native_prompt(command, description, pattern, title):
    from tools import approval
    from tools.approval_context import _fire_approval_hook, _no_user_can_answer, get_current_session_key
    from tools.approval_gateway_wait import _await_gateway_decision
    from tools.approval_prompt import prompt_dangerous_approval
    if _no_user_can_answer():
        return {"resolved": False, "choice": None, "unavailable": True}
    from agent.redact import redact_sensitive_text
    command, description = redact_sensitive_text(command), redact_sensitive_text(description)
    data = {"command": command, "description": description,
            "pattern_key": pattern, "pattern_keys": [pattern],
            "allow_session": False, "allow_permanent": False, "title": title}
    key = get_current_session_key()
    with approval._lock:
        notify = approval._gateway_notify_cbs.get(key)
    if notify is not None:
        return _await_gateway_decision(key, notify, data)
    from tools.terminal_tool import _get_approval_callback
    callback = _get_approval_callback()
    if callback is None:
        return {"resolved": False, "choice": None, "unavailable": True}
    hooks = {k: v for k, v in data.items() if k not in ("allow_session", "allow_permanent")}
    hooks.update(session_key=key, surface="cli")
    _fire_approval_hook("pre_approval_request", **hooks)
    choice = prompt_dangerous_approval(command, description, allow_session=False,
                                      allow_permanent=False, title=title, approval_callback=callback)
    _fire_approval_hook("post_approval_response", **hooks, choice=choice)
    return {"resolved": choice in ("once", "deny"), "choice": choice}


def request(args, session_id=None):
    sid = native._session(session_id)
    if (not sid or not isinstance(args, dict) or set(args) != {"task_id", "plan_revision"}
            or not all(isinstance(v, str) and v for v in args.values())):
        return {"error": "Use the exact same-session task_id and plan_revision; consent cannot be supplied as an argument."}
    tid, revision = args["task_id"], args["plan_revision"]
    key = (sid, tid, revision)
    with _store() as store, closing(h.connect(None)) as conn:
        task = h.get_task(conn, tid)
        if not task or task.session_id != sid:
            return {"error": "Plan approval belongs to a different or missing session delivery."}
        row = store.execute("SELECT result FROM plan_approvals WHERE session_id=? AND task_id=? AND revision=?", key).fetchone()
        if row:
            return json.loads(row["result"])
        snap = _snapshot(conn, task, sid)
        if not snap or snap[0] != revision:
            return {"error": "The plan is unavailable, changed or has unanswered questions; refresh its native context."}
        state = snap[1]
        pending = {"task_id": tid, "status": "approval_pending"}
        store.execute("INSERT INTO plan_approvals VALUES (?, ?, ?, ?)", (*key, json.dumps(pending)))
    # Never hold either DB lock while waiting for the human.
    try:
        decision = _prompt(task, state, revision)
    except Exception:
        decision = {"resolved": False, "choice": None, "unavailable": True}
    result = {"task_id": tid, "status": "waiting_for_approval",
              "note": "No approval received; the delivery remains waiting. Use /cp-build explicitly to retry."}
    if (decision.get("resolved") and decision.get("choice") == "deny"
            and not decision.get("cancelled") and not decision.get("notify_failed")):
        result.update(status="rejected", note="Plan rejected; no build started. Request changes or explicitly approve later.")
    elif (decision.get("resolved") and decision.get("choice") == "once"
          and not any(decision.get(k) for k in ("cancelled", "notify_failed", "unavailable"))):
        with closing(h.connect(None)) as conn:
            with h.kb.write_txn(conn):
                fresh = _snapshot(conn, h.get_task(conn, tid), sid)
                if not fresh or fresh[0] != revision:
                    result.update(status="stale_plan", note="The plan changed while approval was open; no build started.")
                else:
                    # The existing worker consumes this directive, not an invented state mutation.
                    h.comment(conn, tid, "approved", author="cpipe:approved")
                    result.update(status="approved", note="Exact plan approved; implementation queued. No shipping authorization.")
            if result["status"] == "approved" and not h.resume(conn, tid):
                result.update(status="waiting_for_approval", note="Approval recorded but delivery did not resume; check its current state.")
            elif result["status"] == "approved":
                from .tools import _activate
                _activate(tid)
    with _store() as store:
        store.execute("UPDATE plan_approvals SET result=? WHERE session_id=? AND task_id=? AND revision=?",
                      (json.dumps(result), *key))
    return result


def register(ctx):
    if hasattr(ctx, "register_tool"):
        def handler(args, **kwargs):
            try:
                return json.dumps(request(args, kwargs.get("session_id")))
            except Exception as exc:
                return json.dumps({"error": f"Native plan approval failed: {type(exc).__name__}. "
                                   "Check the delivery state before retrying; this error is not approval."})
        ctx.register_tool(name=NAME, toolset="cpipe", schema=SCHEMA, handler=handler)
