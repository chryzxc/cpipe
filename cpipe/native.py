"""Use Hermes-owned project context and clarification, never ordinary-chat starts.

Complete answers to persisted, same-session forms can resume waiting deliveries.
A /cp-build receipt with no existing plan can also start its exact stored request
as a small delivery after native confirmation. Unrelated, stale, replayed,
timed-out and partial responses are no-ops. Shipping and merging are not inferred.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


def session_cwd() -> Path | None:
    """Read the session's directory, not the Desktop's global active project."""
    try:
        from gateway.session_context import get_session_env
        from tools.terminal_tool import get_session_terminal_overrides
        sid = get_session_env("HERMES_SESSION_ID")
        overrides = get_session_terminal_overrides()
        if sid:
            cwd = overrides.get("cwd") if overrides.get("cwd_source") == "session" else None
            return Path(cwd).expanduser() if cwd else None
    except ImportError:
        pass
    return Path.cwd()


def project_repo(name: str | None) -> tuple[str, Path] | None:
    """Read native project identity/folders without changing Hermes project state."""
    try:
        from hermes_cli import projects_db as projects
    except ImportError:
        return None  # older Hermes: explicit legacy project/path remains supported
    with projects.connect_closing() as conn:
        if name:
            project = projects.get_project(conn, name)
            if project is None:
                matches = [p for p in projects.list_projects(conn)
                           if p.name.casefold() == name.casefold()]
                project = matches[0] if len(matches) == 1 else None
        else:
            cwd = session_cwd()
            project = projects.project_for_path(conn, str(cwd)) if cwd else None
        if not project:
            return None
        if not project.primary_path:
            raise ValueError(f"Hermes project {project.slug!r} has no primary folder; select one in Hermes first")
        return project.slug, Path(project.primary_path).expanduser()


def _session(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    try:
        from gateway.session_context import get_session_env
        return get_session_env("HERMES_SESSION_ID")
    except ImportError:
        return os.environ.get("HERMES_SESSION_ID")


@contextmanager
def _store():
    from .home import root as hermes_home
    # Lazy: ordinary chat with no eligible delivery creates no interaction file.
    with closing(sqlite3.connect(hermes_home() / "cpipe-interactions.db", timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE IF NOT EXISTS forms (
            session_id TEXT NOT NULL, task_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            nonce TEXT NOT NULL, answers TEXT NOT NULL, shown TEXT NOT NULL,
            PRIMARY KEY(session_id, task_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS selections (
            session_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            shown TEXT NOT NULL, selected_id TEXT)""")
        conn.commit()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn


def _snapshot(conn, task, session_id: str):
    from . import report
    from .runtime import hermes_api as h
    if (not task or not h.is_delivery(task)
            or task.session_id != session_id or task.status not in ("review", "blocked", "triage")):
        return None
    state = report.state_of(conn, task)
    node = state.get("node")
    if node not in ("proposal", "ask", "handoff", "reviewed", "verified"):
        return None
    if node in ("reviewed", "verified", "handoff"):
        questions = ["Choose the next action for this delivery"]
    elif node == "ask":
        questions = [h.latest_summary(conn, task.id)]
    elif node == "handoff" or not state.get("questions"):
        questions = ["What would you like changed in this delivery?"]
    else:
        questions = state["questions"]
    if (not isinstance(questions, list) or not questions
            or any(not isinstance(q, str) or not q.strip() for q in questions)):
        return None
    run = h.kb.latest_run(conn, task.id)
    signature = json.dumps({"state": state, "questions": questions, "run": run.id if run else None},
                           sort_keys=True, ensure_ascii=False)
    fingerprint = hashlib.sha256(signature.encode()).hexdigest()
    return fingerprint, questions


def _form(store, sid: str, tid: str, fingerprint: str, questions: list[str], choices=None):
    row = store.execute("SELECT * FROM forms WHERE session_id=? AND task_id=?", (sid, tid)).fetchone()
    if row and row["fingerprint"] == fingerprint:
        answers, nonce = json.loads(row["answers"]), row["nonce"]
    else:
        answers, nonce = {}, secrets.token_hex(6)
    shown = [{"question": f"Delivery {tid} [{nonce}:{i + 1}]: {question}"}
             for i, question in enumerate(questions) if str(i) not in answers][:5]
    if choices and shown:
        shown[0]["choices"] = choices
    store.execute("INSERT OR REPLACE INTO forms VALUES (?, ?, ?, ?, ?, ?)",
                  (sid, tid, fingerprint, nonce, json.dumps(answers), json.dumps(shown)))
    return {"questions": shown}


def _selection_signature(eligible):
    return hashlib.sha256(json.dumps(sorted((t.id, snap[0]) for t, snap in eligible)).encode()).hexdigest()


def _selection_choices(eligible):
    return {f"{i + 1}. {task.title}": task.id for i, (task, _) in enumerate(eligible)}


def _selection(store, sid, eligible):
    fingerprint = _selection_signature(eligible)
    row = store.execute("SELECT * FROM selections WHERE session_id=?", (sid,)).fetchone()
    if not row or row["fingerprint"] != fingerprint:
        shown = [{"question": f"[delivery-selection {secrets.token_hex(6)}] Which waiting delivery should we continue? "
                  "If it is not listed, give its title.", "choices": list(_selection_choices(eligible))[:4]}]
        store.execute("INSERT OR REPLACE INTO selections VALUES (?, ?, ?, NULL)",
                      (sid, fingerprint, json.dumps(shown)))
        row = store.execute("SELECT * FROM selections WHERE session_id=?", (sid,)).fetchone()
    return row


def before_turn(*, session_id=None, **_):
    """Supply scoped native-form guidance; no cards, repositories or approvals."""
    sid = _session(session_id)
    if not sid:
        return None
    from .intents import build_start_context
    notice = build_start_context(sid)
    if notice is not None:
        return notice
    from .runtime import hermes_api as h
    with closing(h.connect(None)) as conn:
        eligible = [(t, snapshot) for t in h.list_cards(conn)
                    if (snapshot := _snapshot(conn, t, sid))]
        if not eligible:
            return None
        if len(eligible) > 1:
            with _store() as store:
                choice = _selection(store, sid, eligible)
            selected = [entry for entry in eligible if entry[0].id == choice["selected_id"]]
            if not selected:
                return {"context": "cpipe has several waiting deliveries in this session. Only when discussing "
                        "these deliveries, use Hermes clarify to ask which one; do not guess, resume any, or start "
                        "a new delivery. Selection alone is not delivery feedback.\nNative clarify arguments:\n"
                        + json.dumps({"questions": json.loads(choice["shown"])})}
            task, (fingerprint, questions) = selected[0]
        else:
            task, (fingerprint, questions) = eligible[0]
        from . import plan_approval, action_approval, report
        approval = plan_approval.offer(conn, task, sid)
        if approval is not None:
            return approval
        approval = action_approval.offer(conn, task, sid)
        if approval is not None:
            return approval
        state = report.state_of(conn, task)
        choices = None
        if state.get("node") in ("reviewed", "handoff", "verified"):
            choices = ["Fix findings", "Verify", "Leave waiting"] if state.get("findings") else ["Verify", "Leave waiting"]
        with _store() as store:
            args = _form(store, sid, task.id, fingerprint, questions, choices)
        return {"context": f"cpipe delivery {task.id} is waiting. Only when discussing this delivery, use Hermes' "
                "native clarify tool for its questions or requested changes. For unrelated conversation, ignore "
                "this context. Copy the form below exactly (its delivery marker binds the answers). cpipe persists "
                "complete matching answers and resumes this delivery automatically; do not call cpipe_plan or "
                "infer plan approval, shipping or merging. /cp-build and /cp-ship remain separate explicit actions. "
                "If clarify is unavailable or times out, leave the delivery waiting and report that honestly.\n"
                "Native clarify arguments:\n" + json.dumps(args, ensure_ascii=False)}


def after_tool(*, session_id=None, tool_name=None, args=None, result=None, **_):
    """Consume only native answers to the exact current same-session form."""
    sid = _session(session_id)
    arguments = args
    if not sid or tool_name != "clarify" or not isinstance(arguments, dict):
        return None
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        return None
    if (not isinstance(data, dict) or data.get("error") or data.get("timed_out") or data.get("notice")
            or data.get("outcome", "submitted") != "submitted"):
        return None
    rows = data.get("responses")
    asked = arguments.get("questions")
    if (not isinstance(asked, list) or not isinstance(rows, list) or not asked
            or len(rows) != len(asked)):
        return None
    values = []
    for question, row in zip(asked, rows):
        if (not isinstance(question, dict) or not isinstance(row, dict)
                or not isinstance(question.get("question"), str)
                or row.get("question") not in (question["question"], question["question"].strip())):
            return None
        if row.get("status", "answered") != "answered":
            return None
        value = row.get("user_response")
        if not isinstance(value, str) or not value.strip():
            return None
        values.append(value.strip())
    from .intents import confirm_build_start, confirm_route_start
    started = confirm_route_start(sid, asked, values)
    if started is None:
        started = confirm_build_start(sid, asked, values)
    if started is not None:
        return {**started, "context": "[cpipe build confirmation result] " + json.dumps(started)
                + "\nIf a clarify form is supplied, call clarify with it unchanged to continue routing. Otherwise report this result or check status. Do not ask the user to repeat /cp-plan."}
    from .home import root as hermes_home
    if not (hermes_home() / "cpipe-interactions.db").exists():
        return None
    from .runtime import hermes_api as h
    with _store() as store, closing(h.connect(None)) as conn:
        choice = store.execute("SELECT * FROM selections WHERE session_id=?", (sid,)).fetchone()
        if choice and json.loads(choice["shown"]) == asked:
            eligible = [(t, snap) for t in h.list_cards(conn) if (snap := _snapshot(conn, t, sid))]
            selected_id = _selection_choices(eligible).get(values[0]) if len(values) == 1 else None
            if not selected_id and len(values) == 1:
                matches = [t.id for t, _ in eligible if t.title == values[0]]
                selected_id = matches[0] if len(matches) == 1 else None
            if (_selection_signature(eligible) != choice["fingerprint"] or len(values) != 1
                    or not selected_id):
                return None
            store.execute("UPDATE selections SET selected_id=? WHERE session_id=?", (selected_id, sid))
            return {"selected_task_id": selected_id}
        candidates = store.execute("SELECT * FROM forms WHERE session_id=?", (sid,)).fetchall()
        for pending in candidates:
            if json.loads(pending["shown"]) != asked:
                continue
            from . import report, tools
            task = h.get_task(conn, pending["task_id"])
            snap = _snapshot(conn, task, sid)
            if not snap or snap[0] != pending["fingerprint"]:
                return None
            state = report.state_of(conn, task)
            if state.get("node") in ("reviewed", "handoff", "verified"):
                if len(values) != 1 or values[0] not in (asked[0].get("choices") or []):
                    return None
                if values[0] == "Leave waiting":
                    return {"task_id": task.id, "status": "waiting"}
                result = tools.stage("fix" if values[0] == "Fix findings" else "verify", {"task_id": task.id})
                store.execute("DELETE FROM forms WHERE session_id=? AND task_id=?", (sid, task.id))
                return result
            tools._activate()
            # Validate + record under the board lock. Hermes resume transitions
            # own their transaction, so revalidate after releasing this lock.
            with h.kb.write_txn(conn):
                task = h.get_task(conn, pending["task_id"])
                snapshot = _snapshot(conn, task, sid)
                if not snapshot or snapshot[0] != pending["fingerprint"]:
                    return None
                answers = json.loads(pending["answers"])
                questions = snapshot[1]
                indices = [i for i in range(len(questions)) if str(i) not in answers][:5]
                if len(indices) != len(values):
                    return None
                answers.update({str(i): value for i, value in zip(indices, values)})
                if len(answers) < len(questions):
                    store.execute("UPDATE forms SET answers=?, shown='[]' WHERE session_id=? AND task_id=?",
                                  (json.dumps(answers), sid, task.id))
                    return {"task_id": task.id, "remaining": len(questions) - len(answers)}
                note = "\n\n".join(f"{q}\nAnswer: {answers[str(i)]}" for i, q in enumerate(questions))
                marker = "cpipe:native:" + pending["nonce"]
                if not any(c.author == marker for c in h.comments_after(conn, task.id)):
                    h.comment(conn, task.id, note, author="user")
                    h.comment(conn, task.id, "Native clarification recorded.", author=marker)
            fresh = _snapshot(conn, h.get_task(conn, task.id), sid)
            if not fresh or fresh[0] != pending["fingerprint"] or not h.resume(conn, task.id):
                return None
            store.execute("UPDATE forms SET answers=?, shown='[]' WHERE session_id=? AND task_id=?",
                          (json.dumps(answers), sid, task.id))
            tools._activate(task.id)
            return {"task_id": task.id, "status": h.get_task(conn, task.id).status, "resumed": True}
    return None


def observed_after_tool(*, session_id=None, tool_name=None, args=None, result=None, **kwargs):
    """Observer hooks cannot return to the model; save outcomes for its transformer."""
    sid = _session(session_id)
    questions = (args or {}).get("questions", []) if isinstance(args, dict) else []
    owned = tool_name == "clarify" and any(
        isinstance(q, dict) and q.get("question", "").startswith(("Delivery ", "[delivery-selection "))
        for q in questions)
    try:
        outcome = after_tool(session_id=session_id, tool_name=tool_name, args=args, result=result, **kwargs)
    except Exception as exc:
        if not owned:
            raise
        outcome = {"error": f"Native delivery continuation failed: {type(exc).__name__}",
                   "next": "Report this failure and inspect status; do not infer progress or ask for chat proceed."}
    if owned and sid and outcome is not None:
        from . import intents
        from contextlib import closing
        with closing(intents._connect()) as conn, conn:
            intents._build_notice(conn, "native:" + sid, sid, outcome)
    return outcome


def transform_result(*, session_id=None, tool_name=None, args=None, result=None, **_):
    """Post-tool observers cannot return the next form to the ongoing agent loop."""
    if tool_name != "clarify" or not isinstance(args, dict):
        return None
    questions = args.get("questions")
    if not isinstance(questions, list) or not questions or not all(
            isinstance(q, dict) and isinstance(q.get("question"), str)
            and q["question"].startswith(("[cpipe-build ", "[cpipe-route ", "[cpipe-engine ",
                                          "[cpipe-route-confirm ", "[cpipe-owner ", "Delivery ", "[delivery-selection ")) for q in questions):
        return None
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        return None
    if (not isinstance(data, dict) or data.get("error") or data.get("timed_out") or data.get("notice")
            or data.get("outcome", "submitted") != "submitted"):
        return None
    sid = _session(session_id)
    if not sid:
        return None
    if questions[0]["question"].startswith(("Delivery ", "[delivery-selection ")):
        from .intents import take_build_results
        outcomes = take_build_results(sid)
        if outcomes and any(out.get("error") for out in outcomes):
            return json.dumps({**data, "cpipe_result": outcomes})
        context = before_turn(session_id=sid)
        return json.dumps({**data, "cpipe_result": outcomes, "cpipe_next": context or {
            "context": "Native answers recorded; report current status. Do not request a chat proceed."}})
    from .intents import take_build_results
    outcomes = take_build_results(sid)
    if not outcomes:
        return None
    return json.dumps({**data, "cpipe": outcomes[-1],
        "cpipe_next": "If cpipe supplies a clarify form, call clarify with that exact form. Otherwise report its result. Never repeat the answered form or infer execution consent."})


def register(ctx):
    from . import plan_approval, action_approval
    from .runtime.dispatch_guard import install
    install()
    plan_approval.register(ctx)
    action_approval.register(ctx)
    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_llm_call", before_turn)
        ctx.register_hook("post_tool_call", observed_after_tool)
        ctx.register_hook("transform_tool_result", transform_result)
