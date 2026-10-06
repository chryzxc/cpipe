"""Hermes chat tools (toolset ``cpipe``): the only way a delivery is started or steered.

    cpipe_ship      a new delivery: one card, its own worktree and branch, claimed by lane cp:<engine>
    cpipe_feedback  changes for a delivery: the same card and engine session take another round
    cpipe_ready     accept: final checks, then the pull request is marked ready to merge
    cpipe_status    where a delivery stands (or all of them)
    cpipe_stop      archive a delivery

The chat that ships a delivery is subscribed to it: Hermes notifies it when the pull request is ready to test,
when the delivery needs a decision, and when it completes.
"""

from __future__ import annotations

import json
import re
import secrets
from contextlib import closing
from pathlib import Path

from . import report, settings, workspace_prep
from .runtime import hermes_api

_ID = {"task_id": {"type": "string", "description": "The delivery's card id"}}


def _schema(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required}}


SCHEMAS = {
    "cpipe_ship": _schema(
        "cpipe_ship",
        "Deliver a code change as a tested, reviewed pull request. Use when the user asks to build, fix or ship "
        "something in one of their projects. Returns at once; the user is notified when the pull request is ready "
        "to test.",
        {"request": {"type": "string", "description": "The full task, in the user's words plus any context"},
         "project": {"type": "string", "description": "Project name from the delivery projects (optional when "
                                                      "there is only one)"},
         "title": {"type": "string", "description": "A short title (optional)"},
         "engine": {"type": "string", "description": "Coding engine, only when the user names one (e.g. codex, "
                                                     "claude, or a configured Hermes profile engine)"},
         "model": {"type": "string", "description": "Model, only when the user names one"},
         "size": {"type": "string", "enum": ["small", "large"],
                  "description": "large: plan first, and split into several pull requests when parts are "
                                 "independent"}},
        ["request"]),
    "cpipe_feedback": _schema(
        "cpipe_feedback", "Send the user's requested changes to a delivery; it takes another round in the same "
                          "session and updates its pull request.",
        {**_ID, "text": {"type": "string", "description": "The requested changes"}}, ["task_id", "text"]),
    "cpipe_ready": _schema(
        "cpipe_ready", "The user accepts a delivery: final checks run and its pull request is marked ready to merge.",
        {**_ID, "text": {"type": "string", "description": "Last changes to make first (optional)"}}, ["task_id"]),
    "cpipe_status": _schema("cpipe_status", "Where a delivery stands; without task_id, all open deliveries.",
                            dict(_ID), []),
    "cpipe_stop": _schema("cpipe_stop", "Stop a delivery and archive its card.", dict(_ID), ["task_id"]),
}


def _slug(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower())[:5])[:40] or "change"


def ship(args: dict) -> dict:
    cfg = settings.load()
    projects = {k: v for k, v in workspace_prep.projects().items() if v.get("repo")}
    name = args.get("project") or (next(iter(projects)) if len(projects) == 1 else None)
    if name not in projects:
        return {"error": f"unknown project {name!r}; one of: {', '.join(projects) or 'none configured'}"}
    engine = args.get("engine") or projects[name].get("engine") or cfg["default_engine"]
    if engine not in cfg["engines"]:
        return {"error": f"unknown engine {engine!r}; one of: {', '.join(cfg['engines'])}"}
    request = args["request"].strip()
    title = (args.get("title") or request.splitlines()[0])[:100]
    branch = f"cp/{_slug(title)}-{secrets.token_hex(2)}"
    with closing(hermes_api.connect(None)) as conn:
        tid = hermes_api.create_card(conn, title=title, body=request, assignee=f"cp:{engine}",
                                     repo=str(Path(projects[name]["repo"]).expanduser()), branch=branch,
                                     model=args.get("model"))
        if args.get("size") == "large":
            hermes_api.comment(conn, tid, "plan first", author="cpipe:large")
    return {"task_id": tid, "project": name, "engine": engine, "branch": branch}


def _steer(task_id: str, text: str, directive: str = "") -> dict:
    with closing(hermes_api.connect(None)) as conn:
        task = hermes_api.get_task(conn, task_id)
        if not task or not (task.assignee or "").startswith("cp:"):
            return {"error": f"{task_id} is not a cpipe delivery"}
        if text.strip():
            hermes_api.comment(conn, task_id, text, author="user")
        if directive:
            hermes_api.comment(conn, task_id, directive, author=f"cpipe:{directive}")
        resumed = hermes_api.resume(conn, task_id)
        return {"task_id": task_id, "status": hermes_api.get_task(conn, task_id).status,
                "note": "" if resumed else "queued: the delivery reads it at its next round"}


def feedback(args: dict) -> dict:
    return _steer(args["task_id"], args["text"])


def ready(args: dict) -> dict:
    return _steer(args["task_id"], args.get("text") or "", "ready")


def _brief(conn, task) -> dict:
    st = report.state_of(conn, task)
    return {"task_id": task.id, "title": task.title, "status": task.status, "node": st.get("node"),
            "engine": st.get("engine") or task.assignee[3:], "pr": st.get("pr_url"),
            "verification": st.get("verification")}


def status(args: dict) -> dict:
    with closing(hermes_api.connect(None)) as conn:
        if args.get("task_id"):
            task = hermes_api.get_task(conn, args["task_id"])
            return _brief(conn, task) if task else {"error": f"no card {args['task_id']}"}
        return {"deliveries": [_brief(conn, t) for t in hermes_api.list_cards(conn)
                               if t.status not in ("done", "archived")]}


def stop(args: dict) -> dict:
    with closing(hermes_api.connect(None)) as conn:
        return {"task_id": args["task_id"], "archived": hermes_api.archive(conn, args["task_id"])}


HANDLERS = {"cpipe_ship": ship, "cpipe_feedback": feedback, "cpipe_ready": ready, "cpipe_status": status,
            "cpipe_stop": stop}


def call(name: str, args: dict) -> str:
    try:
        return json.dumps(HANDLERS[name](args or {}))
    except Exception as e:  # a tool error is the chat's to report, never a crash
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def register(ctx) -> None:
    for name, schema in SCHEMAS.items():
        ctx.register_tool(name=name, toolset="cpipe", schema=schema,
                          handler=lambda args, _n=name, **kw: call(_n, args))
