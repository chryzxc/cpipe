"""Hermes chat tools (toolset ``cpipe``): the only way a delivery is started or steered.

    cpipe_ship      a new delivery: one card, its own worktree and branch, claimed by lane cp:<engine>
    cpipe_feedback  changes for a delivery: the same card and engine session take another round
    cpipe_ready     accept: final checks, then the pull request is marked ready to merge
    cpipe_status    where a delivery stands (or all of them)
    cpipe_stop      archive a delivery

The same five are slash commands too, typed straight into a Hermes chat (``/cp-ship <request>``, ...): no model
turn, the answer comes back at once.

The chat that ships a delivery is subscribed to it: Hermes notifies it when the pull request is ready to test,
when the delivery needs a decision, and when it completes.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
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
         "project": {"type": "string", "description": "The project's name (optional when exactly one project is "
                                                      "configured). An unknown project returns a question for the "
                                                      "user"},
         "repo": {"type": "string", "description": "Only with the user's answer to that question: the path to the "
                                                   "project's git checkout, or new to start a new repo. It is saved, "
                                                   "so later deliveries need only the name"},
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


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _key(name: str) -> str:
    return name if re.fullmatch(r"[\w.-]+", name) else _slug(name)


def _checkout(path: Path) -> str | None:
    """Why ``path`` can't be a project's repo, or None when it can."""
    if _git("-C", str(path), "rev-parse", "--show-toplevel").stdout.strip() != str(path.resolve()):
        return f"{path} is not the top of a git repo"
    if _git("-C", str(path), "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
        return f"{path} has no commits yet: commit once first"
    return None


def save_project(name: str, repo: str, cfg: dict) -> tuple[str, Path] | dict:
    """Save where a project's repo is. ``repo`` is the path to its git checkout, or ``new``: a new repo under
    ``projects_root``, started with one empty commit."""
    key = _key(name)
    if repo.strip().lower() == "new":
        path = Path(cfg["projects_root"]).expanduser() / _slug(name)
        if path.exists() and any(path.iterdir()):
            return {"error": f"{path} already exists: answer with its path instead of new"}
        path.mkdir(parents=True, exist_ok=True)
        _git("-C", str(path), "init", "-q", "-b", "main")
        _git("-C", str(path), "commit", "-q", "--allow-empty", "-m", "chore: start")
    else:
        path = Path(repo.strip()).expanduser()
        why = _checkout(path)
        if why:
            return {"error": why}
    workspace_prep.save_repo(key, str(path))
    return key, path


def resolve_project(project: str | None, cfg: dict) -> tuple[str, Path] | dict:
    """``(name, repo)`` from projects.yaml, or what to ask the user: ``{"question", "choices", "project"}``."""
    known = {k: v for k, v in workspace_prep.projects().items() if v.get("repo")}
    if not project:
        if len(known) == 1:
            project = next(iter(known))
        else:
            return {"question": "Which project is this for?", "choices": [*known, "a new project (give it a name)"]}
    for name in (project, _key(project)):
        if name in known:
            return name, Path(known[name]["repo"]).expanduser()
    if project.startswith(("/", "~")):
        return save_project(Path(project).expanduser().name, project, cfg)
    guess = Path(cfg["projects_root"]).expanduser() / _slug(project)
    choices = ([str(guess)] if guess.is_dir() and not _checkout(guess) else []) + ["new"]
    return {"project": project, "choices": choices,
            "question": f"Where is the repo for {project}? The path to its git checkout, or new to start one at "
                        f"{guess}. It is saved for next time."}


def ship(args: dict) -> dict:
    cfg = settings.load()
    if args.get("repo"):
        found = save_project(args.get("project") or Path(args["repo"]).expanduser().name, args["repo"], cfg)
    else:
        found = resolve_project(args.get("project"), cfg)
    if isinstance(found, dict):
        return found if "error" in found else {**found, "next": "Ask the user (clarify), then call cpipe_ship "
                                                               "again with project and repo set from the answer."}
    name, repo = found
    known = workspace_prep.projects().get(name) or {}
    engine = args.get("engine") or known.get("engine") or cfg["default_engine"]
    if engine not in cfg["engines"]:
        return {"error": f"unknown engine {engine!r}; one of: {', '.join(cfg['engines'])}"}
    request = args["request"].strip()
    title = (args.get("title") or request.splitlines()[0])[:100]
    branch = f"cp/{_slug(title)}-{secrets.token_hex(2)}"
    with closing(hermes_api.connect(None)) as conn:
        tid = hermes_api.create_card(conn, title=title, body=request, assignee=f"cp:{engine}",
                                     repo=str(repo), branch=branch,
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




# --- slash commands: /cp-ship, /cp-feedback, /cp-ready, /cp-status, /cp-stop ---

def _text(out: dict) -> str:
    if out.get("error"):
        return f"cpipe: {out['error']}"
    if out.get("question"):
        name = out.get("project") or "<name>"
        return (f"cpipe: {out['question']}\nAnswer with /cp-project {name} <path>"
                + (" (or new)" if "new" in out["choices"] else "") + ", then ship again."
                + ("\nChoices: " + ", ".join(out["choices"]) if out["choices"] else ""))
    if "deliveries" in out:
        return "\n".join(_text(d) for d in out["deliveries"]) or "cpipe: no open deliveries"
    parts = [out.get("task_id", ""), out.get("title") or out.get("project") or "", out.get("status") or "",
             out.get("node") and f"at {out['node']}", out.get("engine") and f"on {out['engine']}",
             out.get("branch") or "", out.get("pr") or "", out.get("note") or "",
             "archived" if out.get("archived") else ""]
    return "  ".join(p for p in parts if p)


def _split(raw: str) -> tuple[str, str]:
    head, _, rest = raw.strip().partition(" ")
    return head, rest.strip()


def _ship_args(raw: str) -> dict:
    """``[project:] request``: a leading ``name:`` (or ``path:``) picks the project."""
    head, sep, rest = raw.strip().partition(": ")
    if sep and rest.strip() and 0 < len(head) <= 60 and "\n" not in head:
        return {"project": head, "request": rest.strip()}
    return {"request": raw}


def _project(raw: str) -> dict:
    name, _, repo = raw.strip().rpartition(" ")   # the last word is the path (or new): names may have spaces
    if not name.strip():
        return {"error": "usage: /cp-project <name> <path|new>"}
    found = save_project(name.strip(), repo, settings.load())
    return found if isinstance(found, dict) else {"project": found[0], "note": f"saved: {found[1]}"}


COMMANDS = {
    "cp-ship": ("Start a delivery", "[project:] <request>", lambda raw: ship(_ship_args(raw))),
    "cp-project": ("Save where a project's repo is (new starts one)", "<name> <path|new>", lambda raw: _project(raw)),
    "cp-feedback": ("Send changes to a delivery", "<card> <changes>",
                    lambda raw: feedback(dict(zip(("task_id", "text"), _split(raw))))),
    "cp-ready": ("Accept a delivery: final checks, PR ready to merge", "<card> [last changes]",
                 lambda raw: ready(dict(zip(("task_id", "text"), _split(raw))))),
    "cp-status": ("Where deliveries stand", "[card]", lambda raw: status({"task_id": raw.strip()})),
    "cp-stop": ("Stop a delivery and archive its card", "<card>", lambda raw: stop({"task_id": raw.strip()})),
}


def command(name: str, raw: str) -> str:
    desc, hint, fn = COMMANDS[name]
    if "<" in hint and not (raw or "").strip():
        return f"usage: /{name} {hint}"
    try:
        return _text(fn(raw or ""))
    except Exception as e:  # never crash the chat
        return f"cpipe: {type(e).__name__}: {e}"


def register(ctx) -> None:
    for name, schema in SCHEMAS.items():
        ctx.register_tool(name=name, toolset="cpipe", schema=schema,
                          handler=lambda args, _n=name, **kw: call(_n, args))
    if hasattr(ctx, "register_command"):
        for name, (desc, hint, _) in COMMANDS.items():
            ctx.register_command(name, lambda raw="", _n=name: command(_n, raw), description=desc, args_hint=hint)
