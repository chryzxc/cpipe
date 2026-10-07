"""Session-aware slash-command intents, delivered through Hermes' native queue.

A command is an explicit user action, not a positional delivery-id parser. An
opaque, expiring receipt allows only that command's backend operation; normal
conversation cannot start a delivery through the bridge. Stage commands adopt
existing committed work and run specialist gates; they never bypass the runner.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from contextlib import closing

from .home import root

POLICIES = {
    "cp-plan": "Plan the requested work using this conversation. Resolve the repository, full request and optional --size/project hints from session context. Use the authorized backend plan operation only for an explicitly requested cpipe delivery; default to large when size is unspecified.",
    "cp-build": "Approve and build the matching cpipe plan with the configured builder, tests-first, independent review and verification. Use its same-session task_id. If none exists, resolve the full request and repository, then call the build operation without task_id, supplying request and project or repo. The backend returns an exact native clarify form: call clarify with that form unchanged. Its confirmation starts the small delivery automatically; report the hook result or use status, never ask the user to repeat /cp-plan and never call plan with this receipt. To review already implemented work, use /cp-review instead. Never implement this command in the coordinator session.",
    "cp-review": "Independently review this work using the configured reviewer. Resolve the existing same-session task_id; otherwise adopt the current committed checkout with request/project/base. Stop locally after review; no edits or publishing. Findings return for /cp-fix, then /cp-verify.",
    "cp-fix": "Have the configured builder fix the specified review findings or bounded improvements; reuse its warm session. Then independently review and verify the resulting revision. Use the same-session task_id, or adopt the existing committed work. No publishing.",
    "cp-verify": "Run deterministic checks, independent review and verifier/QA on the final committed revision. Reuse valid exact-revision receipts. Use the same-session task_id or adopt existing work. Stop locally; no publishing. Missing required checks are not passes.",
    "cp-ship": "Complete this work before publishing: independently review behavior, security, test adequacy and justified scoped refactors; have the builder add missing tests and make bounded corrections, then re-review and verify focused tests, regression checks and applicable QA on the final revision. Reuse only receipts that satisfy this completion contract. Use the same-session task_id, or adopt earlier work without a cpipe delivery. Use the backend ship operation, never a direct-tools bypass. Default to draft; ready=true only when the user explicitly asks to mark it ready. Never merge, deploy or publish a release.",
    "cp-status": "Report current status of the work discussed here from real Git/PR/test or matching cpipe evidence. Do not mutate work or start a delivery.",
    "cp-stop": "Stop only the work or existing cpipe delivery identified by this conversation and the user's message. Do not delete files, reset branches, archive unrelated deliveries or stop unrelated services.",
}

SCHEMA = {
    "name": "cpipe_execute_intent",
    "description": "Execute the cpipe backend operation authorized by a specific explicit /cp-* command. Requires its receipt token. Ordinary chat has no receipt and cannot start a delivery. Stage commands adopt existing work and dispatch configured specialist profiles.",
    "parameters": {"type": "object", "properties": {
        "token": {"type": "string"},
        "operation": {"type": "string", "enum": ["plan", "build", "review", "fix", "verify", "ship", "status", "stop"]},
        "arguments": {"type": "object", "description": "Resolved scope: request/project/size for plan; task_id/text for an existing delivery, or request/project/base to adopt committed work; ready=true only when explicitly requested."},
    }, "required": ["token", "operation", "arguments"]},
}


def _session_key():
    from gateway.session_context import get_session_env
    return get_session_env("HERMES_SESSION_KEY") or get_session_env("HERMES_SESSION_ID") or ""


def _connect():
    conn = sqlite3.connect(root() / "cpipe-intents.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS intents (
        token TEXT PRIMARY KEY, operation TEXT NOT NULL, session_key TEXT NOT NULL,
        expires REAL NOT NULL, state TEXT NOT NULL, result TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS build_notices (
        token TEXT PRIMARY KEY, session_id TEXT NOT NULL, result TEXT NOT NULL)""")
    conn.commit()
    return conn


def dispatch(ctx, name, message=""):
    """Queue a native user turn; never run an id parser or backend here."""
    if name not in POLICIES:
        return "cpipe: unknown command"
    if not hasattr(ctx, "inject_message"):
        return "cpipe: this Hermes host cannot queue session-aware commands; update Hermes. No work started."
    token = secrets.token_urlsafe(24)
    key = _session_key()
    operation = name[3:]
    prompt = (f"The user explicitly invoked /{name} in this conversation. Use the existing session history "
              "and native project context as reference; the message below is normal user guidance, not a delivery id.\n"
              + POLICIES[name] + "\nResolve targets from evidence; use native clarify only if genuinely ambiguous. "
              "Preserve unrelated changes and existing approval boundaries. Do the work with tools, then report real "
              "verification. Do not claim queued work has completed. An optional delivery id is only a reference. "
              "Do not infer scope, base branch or approvals from plugin defaults.\n"
              f"For cpipe backend work only, cpipe_execute_intent receipt: {token}. "
              f"Permitted operation: {operation}; status lookup is also allowed. "
              "Use the matching same-session delivery, never an unrelated card.\n"
              "User message (preserved verbatim):\n" + message)
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM intents WHERE expires < ?", (time.time(),))
        conn.execute("INSERT INTO intents VALUES (?, ?, ?, ?, 'pending', NULL)",
                     (token, operation, key, time.time() + 86400))
    try:
        accepted = ctx.inject_message(prompt, role="user", session_key=key or None)
    except Exception:
        accepted = False
    if not accepted:
        with closing(_connect()) as conn, conn:
            conn.execute("DELETE FROM intents WHERE token=?", (token,))
        return ("cpipe: could not queue the command in this session. No work started. "
                "Desktop/TUI requires an existing session and the plugin's allow_gateway_injection permission.")
    return f"cpipe: /{name} queued — resolving this conversation, then handing off to specialists. Results follow here."


def _active_delivery(sid):
    from .runtime import hermes_api as h
    with closing(h.connect(None)) as board:
        return any(t.session_id == sid and (t.assignee or "").startswith("cp:")
                   and t.status not in ("done", "archived") for t in h.list_cards(board))


def _build_start_form(conn, receipt, payload):
    """Persist a scope-bound form; a tool argument cannot approve new work."""
    from .native import _session
    sid = _session(None)
    if not sid or _active_delivery(sid):
        return {"error": "resolve the existing same-session cpipe delivery before building"}
    if receipt["result"]:
        return json.loads(receipt["result"])
    request = payload.get("request")
    project = payload.get("project") or payload.get("repo")
    if not isinstance(request, str) or not request.strip() or not isinstance(project, str) or not project.strip():
        return {"error": "resolve request and project or repo before confirming a new build"}
    scope = {k: payload[k] for k in ("request", "project", "repo", "engine", "model", "title") if k in payload}
    scope["size"] = "small"
    form = {"questions": [{
        "question": f"[cpipe-build {secrets.token_hex(12)}] No plan exists. Start a small delivery for {project}?\n\n{request}",
        "choices": ["Start a small delivery", "Cancel"],
    }]}
    result = {"status": "needs_confirmation", "session_id": sid, "arguments": scope, "clarify": form,
              "next": "Call native clarify with the supplied form unchanged. Confirmation starts this delivery automatically; do not request another slash command."}
    conn.execute("UPDATE intents SET result=? WHERE token=?", (json.dumps(result), receipt["token"]))
    return result


def _build_notice(conn, token, sid, result):
    conn.execute("INSERT OR REPLACE INTO build_notices VALUES (?, ?, ?)", (token, sid, json.dumps(result)))


def build_start_context(sid):
    """Post-tool hooks are observers; deliver their outcome through pre-LLM context."""
    if not (root() / "cpipe-intents.db").exists():
        return None
    with closing(_connect()) as conn, conn:
        rows = conn.execute("SELECT result FROM build_notices WHERE session_id=?", (sid,)).fetchall()
        if not rows:
            return None
        conn.execute("DELETE FROM build_notices WHERE session_id=?", (sid,))
    return {"context": "[cpipe build confirmation results]\n" + "\n".join(row["result"] for row in rows)
            + "\nReport the result or check status. Do not request another /cp-plan: the confirmation has been handled. Do not infer shipping or merging permission."}


def confirm_build_start(sid, asked, values):
    """Consume a native confirmation once, using only the previously stored scope."""
    if not (root() / "cpipe-intents.db").exists() or len(values) != 1:
        return None
    from . import tools
    with closing(_connect()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT * FROM intents WHERE operation='build' AND state='pending' AND result IS NOT NULL").fetchall()
            for receipt in rows:
                pending = json.loads(receipt["result"])
                if (pending.get("session_id") != sid or pending.get("clarify", {}).get("questions") != asked
                        or receipt["expires"] < time.time() or not receipt["session_key"]
                        or receipt["session_key"] != _session_key()):
                    continue
                if values[0] not in ("Start a small delivery", "Cancel"):
                    return None
                if values[0] == "Cancel" or _active_delivery(sid):
                    result = ({"status": "cancelled"} if values[0] == "Cancel" else
                              {"error": "a same-session delivery appeared; resolve it instead of starting another"})
                    conn.execute("UPDATE intents SET state='complete', result=? WHERE token=?",
                                 (json.dumps(result), receipt["token"]))
                    _build_notice(conn, receipt["token"], sid, result)
                    return result
                conn.execute("UPDATE intents SET state='claimed' WHERE token=?", (receipt["token"],))
                break
            else:
                return None
        try:
            result = tools.HANDLERS["cpipe_plan"](pending["arguments"])
        except Exception as exc:
            # The handler may already have submitted a card. Never replay it.
            result = {"error": f"{type(exc).__name__}: {exc}; inspect status before reissuing the command"}
            with conn:
                _build_notice(conn, receipt["token"], sid, result)
            return result
        with conn:
            conn.execute("UPDATE intents SET state='complete', result=? WHERE token=?",
                         (json.dumps(result), receipt["token"]))
            _build_notice(conn, receipt["token"], sid, result)
        return result


def execute(args, **_):
    """Fail closed without a valid receipt; cache a completed mutation on replay."""
    from . import tools
    from .runtime import hermes_api as h
    token, operation = args.get("token"), args.get("operation")
    payload = args.get("arguments")
    if not isinstance(payload, dict) or operation not in {n[3:] for n in POLICIES}:
        return json.dumps({"error": "invalid operation or arguments"})
    if not (root() / "cpipe-intents.db").exists():
        return json.dumps({"error": "an explicit /cp-* command is required"})
    with closing(_connect()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            receipt = conn.execute("SELECT * FROM intents WHERE token=?", (token,)).fetchone()
            if (not receipt or receipt["expires"] < time.time()
                    or (receipt["session_key"] and receipt["session_key"] != _session_key())
                    or operation not in (receipt["operation"], "status")):
                return json.dumps({"error": "missing, expired, wrong-session or incompatible command receipt"})
            if operation != "status":
                if receipt["state"] == "complete":
                    return receipt["result"]
                if receipt["state"] != "pending":
                    return json.dumps({"error": "operation already claimed; inspect status before retrying"})
                if operation == "build" and not payload.get("task_id"):
                    return json.dumps(_build_start_form(conn, receipt, payload))
                if operation not in ("plan", "review", "fix", "verify", "ship") or payload.get("task_id"):
                    from .native import _session
                    sid = _session(None)
                    with closing(h.connect(None)) as board:
                        task = h.get_task(board, payload.get("task_id", ""))
                    if not sid or not task or task.session_id != sid or not (task.assignee or "").startswith("cp:"):
                        return json.dumps({"error": "no matching same-session cpipe delivery; clarify the target"})
                conn.execute("UPDATE intents SET state='claimed' WHERE token=?", (token,))
        try:
            if operation == "status":
                from .native import _session
                sid = _session(None)
                with closing(h.connect(None)) as board:
                    result = {"deliveries": [tools._brief(board, task) for task in h.list_cards(board)
                                              if sid and task.session_id == sid and task.status not in ("done", "archived")]}
            elif operation in ("review", "fix", "verify", "ship"):
                result = tools.stage(operation, payload)
            else:
                result = tools.HANDLERS["cpipe_" + operation](payload)
            encoded = json.dumps(result)
        except Exception as exc:
            # A handler may have committed before raising: never automatically replay it.
            return json.dumps({"error": f"{type(exc).__name__}: {exc}; inspect status before reissuing the command"})
        if operation != "status":
            with conn:
                conn.execute("UPDATE intents SET state='complete', result=? WHERE token=?", (encoded, token))
        return encoded


def register(ctx):
    ctx.register_tool(name=SCHEMA["name"], toolset="cpipe", schema=SCHEMA, handler=execute)
    for name in POLICIES:
        ctx.register_command(name, lambda raw="", _name=name: dispatch(ctx, _name, raw),
                             description="Use this session's context to " + name[3:] + " the requested work",
                             args_hint="[message]", argument_mode="text")
