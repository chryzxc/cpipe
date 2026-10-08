"""Explicit Hermes slash commands, with optional model tools (toolset ``cpipe``). One delivery goes
plan → build → ship, and the user decides at each step:

    cpipe_plan      a new delivery: one card, its own worktree and branch; the engine plans, and the plan (with
                    its questions) comes back for the user's approval
    native clarify answers and changes for the waiting plan/change: the same session takes another round
    cpipe_build     approve the plan: build, checks, QA, security, review, a draft pull request to test
    cpipe_ship      accept the change: final checks, then the pull request is marked ready for review
    cpipe_status    where a delivery stands (or all of them)
    cpipe_stop      archive a delivery

Slash commands enqueue natural-language intents in the current Hermes session. The receipt-gated
bridge resolves their backend arguments from conversation context; late entry adopts committed work.
Unrestricted legacy model tools are exposed only with ``command_only: false``. Hermes owns project
selection. This is opt-in routing, not a sandbox against deliberate shell/Python invocation.

The chat that starts a delivery is subscribed to it: Hermes notifies it when the plan is ready, when the pull
request is ready to test, when a decision is needed, and when it completes.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
from contextlib import closing
from pathlib import Path

from . import native, report, settings, workspace_prep
from .runtime import hermes_api

_ID = {"task_id": {"type": "string", "description": "The delivery's card id"}}


def _schema(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required}}


SCHEMAS = {
    "cpipe_plan": _schema(
        "cpipe_plan",
        "Start a development task in one of the user's projects: the coding engine studies the repo and writes a "
        "plan, with questions, for approval (size=large, the default). Use size=small only for a clear, authorized "
        "logic change in one area, or content for text/styles/docs only: those start implementation immediately, "
        "still get independent review and pause before shipping. Unclear or cross-boundary work must stay large. "
        "Returns at once; the user is notified at the next approval boundary.",
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
         "size": {"type": "string", "enum": ["large", "small", "content"], "default": "large",
                  "description": "large: approved plan first; small: clear authorized logic change; content: text/styles/docs only. Fast paths authorize implementation, not shipping."},
         },
        ["request"]),
    "cpipe_build": _schema(
        "cpipe_build", "The user approves a delivery's plan: it is built, tested, QA'd, reviewed, and opened as a "
                       "draft pull request to test.",
        {**_ID, "text": {"type": "string", "description": "Notes to build with (optional)"}}, ["task_id"]),
    "cpipe_ship": _schema(
        "cpipe_ship", "The user accepts the built change: final checks run and its pull request is marked ready "
                      "for review.",
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
    """Hermes identity/session first; legacy delivery configuration is a fallback.

    Resolving a path is read-only. Project setup and selection belong to Hermes.
    Never infer the target from a different Desktop session's active project.
    """
    found = native.project_repo(project)
    if found:
        return found
    known = {k: v for k, v in workspace_prep.projects().items() if v.get("repo")}
    if not project:
        cwd = native.session_cwd()
        matches = [(name, Path(value["repo"]).expanduser()) for name, value in known.items()
                   if cwd and cwd.resolve().is_relative_to(Path(value["repo"]).expanduser().resolve())]
        if len(matches) == 1:
            return matches[0]
        return {"question": "Which project is this for? Select it in Hermes, or give its name or checkout path.",
                "choices": list(known)}
    for name in (project, _key(project)):
        if name in known:
            return name, Path(known[name]["repo"]).expanduser()
    if project.startswith(("/", "~")):
        path = Path(project).expanduser()
        why = _checkout(path)
        return {"error": why} if why else (_key(path.name), path)
    return {"project": project, "choices": [],
            "question": f"Where is the checkout for {project}? Select or create its project in Hermes first."}


def _activate(task_id=None):
    from .runtime import activation
    activation.ensure_running()
    if task_id:
        result = activation.nudge()
        if result.errors:
            raise RuntimeError("Delivery dispatch failed; inspect status: " + ", ".join(result.errors))
        if task_id in result.spawn_failed:
            raise RuntimeError("Delivery supervisor could not start; the card records the launch failure")


def plan(args: dict) -> dict:
    size = args.get("size", "large")
    if size not in ("large", "small", "content"):
        return {"error": "size must be large, small, or content"}
    cfg = settings.load()
    if args.get("repo"):
        found = save_project(args.get("project") or Path(args["repo"]).expanduser().name, args["repo"], cfg)
    else:
        found = resolve_project(args.get("project"), cfg)
    if isinstance(found, dict):
        return found if "error" in found else {**found, "next": "Use Hermes native clarify/project selection. "
                                              "No delivery was started. After selecting the checkout, rerun "
                                              "the explicit /cp-plan command; ordinary answers never start one."}
    name, repo = found
    if why := _checkout(repo):
        return {"error": why}
    if args.get("routing"):
        from .routing import effective_config
        cfg = effective_config(cfg, args["routing"])
    known = workspace_prep.projects().get(name) or {}
    engine = args.get("engine") or known.get("engine") or cfg.get("roles", {}).get("build") or cfg["default_engine"]
    if engine not in cfg["engines"]:
        return {"error": f"unknown engine {engine!r}; one of: {', '.join(cfg['engines'])}"}
    request = args["request"].strip()
    title = (args.get("title") or request.splitlines()[0])[:100]
    branch = f"cp/{_slug(title)}-{secrets.token_hex(2)}"
    _activate()
    with closing(hermes_api.connect(None)) as conn:
        from . import engines
        owner = cfg.get("roles", {}).get("plan", engine) if size == "large" else engine
        ec = cfg["engines"][owner]
        assignee = (ec.get("profile") or "default") if engines.kind(owner, ec) == "hermes" else engines.kind(owner, ec)
        tid = hermes_api.create_card(conn, title=title, body=request, assignee=assignee, engine=engine,
                                     repo=str(repo), branch=branch,
                                     model=args.get("model"), size=size, routing=args.get("routing"))
    _activate(tid)
    return {"task_id": tid, "project": name, "engine": engine, "assignee": assignee,
            "branch": branch, "size": size, "runner_ready": True}


def _steer(task_id: str, text: str, directive: str = "") -> dict:
    with closing(hermes_api.connect(None)) as conn:
        task = hermes_api.get_task(conn, task_id)
        if not hermes_api.is_delivery(task):
            return {"error": f"{task_id} is not a cpipe delivery"}
        if task.status in ("done", "archived"):
            return {"error": "Delivery is closed; no directive was recorded"}
        _activate()
        if text.strip():
            hermes_api.comment(conn, task_id, text, author="user")
        if directive:
            hermes_api.comment(conn, task_id, directive, author=f"cpipe:{directive}")
        resumed = hermes_api.resume(conn, task_id)
        _activate(task_id)
        return {"task_id": task_id, "status": hermes_api.get_task(conn, task_id).status,
                "note": "" if resumed else "queued: the delivery reads it at its next round"}


def feedback(args: dict) -> dict:
    return _steer(args["task_id"], args["text"])


def stage(entry: str, args: dict) -> dict:
    """Enter a specialist stage on the owning card, or snapshot existing committed work.

    Review is read-only. Fix/verify may run bounded rework. Only ship authorizes
    publication, draft by default. Source checkout, branch and history stay intact.
    """
    if entry not in ("review", "fix", "verify", "ship"):
        return {"error": "unknown collaborative stage"}
    if not isinstance(args.get("ready", False), bool):
        return {"error": "ready must be a boolean"}
    if entry != "ship" and args.get("ready"):
        return {"error": "only /cp-ship can authorize PR readiness"}
    if args.get("task_id"):
        if not re.fullmatch(r"t_[0-9a-f]{8}", args["task_id"]):
            return {"error": "invalid task_id; resolve the matching delivery from this session"}
        with closing(hermes_api.connect(None)) as conn:
            task = hermes_api.get_task(conn, args["task_id"])
            if not hermes_api.is_delivery(task) or task.status in ("done", "archived"):
                return {"error": "no open cpipe delivery with that id"}
            if task.status == "running":
                return {"error": "delivery is running; wait for its handoff before entering another stage"}
            state = report.state_of(conn, task)
            if state.get("node") in ("setup", "plan", "split", "proposal", "tests"):
                return {"error": "this delivery has not built a revision yet; approve it with /cp-build first"}
            directive = {"entry": entry, "publish": entry == "ship", "ready": args.get("ready", False),
                         "text": (args.get("text") or "").strip()}
            if entry == "fix" and not directive["text"] and not state.get("findings"):
                return {"question": "What findings or bounded improvements should the builder fix?"}
            _activate()
            hermes_api.comment(conn, task.id, json.dumps(directive), "cpipe:stage")
            hermes_api.resume(conn, task.id)
            _activate(task.id)
            return {"task_id": task.id, "stage": entry, "note": "Specialist stage queued; results follow here."}

    from . import routing
    cfg = settings.load()
    if args.get("routing"):
        cfg = routing.effective_config(cfg, args["routing"])
    found = resolve_project(args.get("project") or args.get("repo"), cfg)
    if isinstance(found, dict):
        return {**found, "next": "Use native clarify/project selection; no workflow started."}
    name, repo = found
    if why := _checkout(repo):
        return {"error": why}
    dirty = _git("-C", str(repo), "status", "--porcelain", "--untracked-files=normal")
    if dirty.returncode or dirty.stdout.strip():
        return {"error": "Existing work must be committed in a clean checkout before adoption. "
                         "No files were stashed, committed or reset."}
    request = str(args.get("request") or "").strip()
    if not request:
        return {"question": "What should this existing change accomplish? Resolve its scope from this conversation."}
    known = workspace_prep.projects().get(name) or {}
    base = args.get("base") or known.get("base_branch") or workspace_prep.default_branch(repo)
    if not isinstance(base, str) or _git("check-ref-format", "--branch", base).returncode:
        return {"error": "base must be a valid branch name"}
    ref = next((r for r in (f"refs/remotes/origin/{base}", f"refs/heads/{base}")
                if _git("-C", str(repo), "rev-parse", "--verify", "-q", r).returncode == 0), None)
    if not ref:
        return {"error": f"PR base {base!r} is unavailable locally; fetch or choose its correct branch first"}
    sha = _git("-C", str(repo), "rev-parse", "HEAD").stdout.strip()
    merge = _git("-C", str(repo), "merge-base", ref, sha)
    if merge.returncode:
        return {"error": "source revision and PR base have no shared history"}
    changed = _git("-C", str(repo), "diff", "--name-only", merge.stdout.strip(), sha)
    if changed.returncode or not changed.stdout.strip():
        return {"error": "there is no committed change against the PR base to adopt"}
    engine = args.get("engine") or known.get("engine") or cfg.get("roles", {}).get("build") or cfg["default_engine"]
    if engine not in cfg["engines"]:
        return {"error": f"unknown engine {engine!r}"}
    title = request.splitlines()[0][:100]
    branch = f"cp/{_slug(title)}-{secrets.token_hex(4)}"
    _activate()
    # Pre-create a branch at the exact receipt: dispatcher never guesses source HEAD.
    created = _git("-C", str(repo), "branch", branch, sha)
    if created.returncode:
        return {"error": f"could not snapshot source revision: {created.stderr.strip()}"}
    adoption = {"entry": entry, "publish": entry == "ship", "ready": args.get("ready", False),
                "head": sha, "base": base, "base_head": _git("-C", str(repo), "rev-parse", ref).stdout.strip(),
                "source_branch": _git("-C", str(repo), "branch", "--show-current").stdout.strip(),
                "request": request}
    with closing(hermes_api.connect(None)) as conn:
        from . import engines
        owner = cfg.get("roles", {}).get("review", engine) if entry == "review" else engine
        ec = cfg["engines"][owner]
        assignee = (ec.get("profile") or "default") if engines.kind(owner, ec) == "hermes" else engines.kind(owner, ec)
        tid = hermes_api.create_card(conn, title=title, body=request, assignee=assignee, engine=engine, model=None,
                                     repo=str(repo), branch=branch, idempotency_key=f"cpipe:adopt:{branch}",
                                     max_runtime_seconds=(int(cfg["limits"]["round_minutes"]) + settings.PR_MINUTES) * 60,
                                     adoption=adoption, routing=args.get("routing"))
        _activate(tid)
        return {"task_id": tid, "project": name, "branch": branch, "stage": entry,
                "source_head": sha, "note": "Existing work adopted; no retrospective TDD claim. Specialist results follow here."}


def build(args: dict) -> dict:
    return _steer(args["task_id"], args.get("text") or "", "approved")


def ship(args: dict) -> dict:
    return _steer(args["task_id"], args.get("text") or "", "ready")


STAGES = {"setup": "planning", "plan": "planning", "proposal": "plan ready: approve with /cp-build",
          "ask": "needs your answer", "handoff": "ready to test: accept with /cp-ship", "final": "shipping",
          "done": "shipped", "stuck": "stuck", "paused": "paused",
          "review_stage": "reviewing", "reviewed": "review complete", "verified": "verification complete"}


def _brief(conn, task) -> dict:
    st = report.state_of(conn, task)
    node = st.get("node") or "setup"
    out = {"task_id": task.id, "title": task.title, "status": task.status,
           "stage": STAGES.get(node, "planning" if not st.get("approved") else "building"),
           "engine": st.get("engine") or hermes_api.engine_of(conn, task), "pr": st.get("pr_url"),
           "verification": st.get("verification"), "findings": st.get("findings"),
           "routing": st.get("routing"),
           "source_head": (st.get("adoption") or {}).get("head"), "verified_head": st.get("verified_head")}
    if node == "proposal":
        out.update(plan=st.get("plan"), questions=st.get("questions"))
    return out


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


HANDLERS = {"cpipe_plan": plan, "cpipe_build": build, "cpipe_ship": ship,
            "cpipe_status": status, "cpipe_stop": stop}


def call(name: str, args: dict) -> str:
    try:
        # Recheck at invocation so a cached registration cannot bypass a mode change.
        if settings.load()["command_only"]:
            from .intents import optional_workflow_error
            return json.dumps(optional_workflow_error())
        return json.dumps(HANDLERS[name](args or {}))
    except Exception as e:  # a tool error is the chat's to report, never a crash
        return json.dumps({"error": f"{type(e).__name__}: {e}"})




# --- explicit delivery lifecycle commands; Hermes owns interaction/project setup ---

def _text(out: dict) -> str:
    if out.get("error"):
        return f"cpipe: {out['error']}"
    if out.get("question"):
        return (f"cpipe: {out['question']}\nUse Hermes native project selection/clarify, then rerun /cp-plan. "
                "No delivery was started."
                + ("\nChoices: " + ", ".join(out["choices"]) if out.get("choices") else ""))
    if "deliveries" in out:
        return "\n".join(_text(d) for d in out["deliveries"]) or "cpipe: no open deliveries"
    parts = [out.get("task_id", ""), out.get("title") or out.get("project") or "",
             out.get("stage") or out.get("status") or "", out.get("engine") and f"on {out['engine']}",
             out.get("branch") or "", out.get("pr") or "", out.get("note") or "",
             "archived" if out.get("archived") else ""]
    line = "  ".join(p for p in parts if p)
    if out.get("plan"):
        line += "\n\n" + out["plan"]
        if out.get("questions"):
            line += "\n\nQuestions:" + "".join(f"\n- {q}" for q in out["questions"])
    return line


def _split(raw: str) -> tuple[str, str]:
    head, _, rest = raw.strip().partition(" ")
    return head, rest.strip()


def _plan_args(raw: str) -> dict:
    """Leading size option, then the existing optional ``project: request`` syntax."""
    usage = "usage: /cp-plan [--size content|small|large] [project:] <idea>"
    raw = raw.strip()
    size = "large"
    first = raw.split(None, 1)[0] if raw else ""
    if first == "--size":
        parts = raw.split(None, 2)
        if len(parts) != 3:
            raise ValueError(usage)
        _, size, raw = parts
    elif first.startswith("--size="):
        parts = raw.split(None, 1)
        if len(parts) != 2:
            raise ValueError(usage)
        option, raw = parts
        size = option.partition("=")[2]
    if size not in ("content", "small", "large") or not raw or raw.startswith("-"):
        raise ValueError(usage)
    head, sep, rest = raw.partition(": ")
    if sep and len(head) <= 60 and "\n" not in head:
        if not head.strip() or not rest.strip():
            raise ValueError(usage)
        return {"project": head.strip(), "request": rest.strip(), "size": size}
    if raw.endswith(":") and " " not in raw:
        raise ValueError(usage)
    return {"request": raw, "size": size}


COMMANDS = {
    "cp-plan": ("Start an explicit delivery: large plans first; small/content start implementation",
                "[--size content|small|large] [project:] <idea>",
                lambda raw: plan(_plan_args(raw))),

    "cp-build": ("Approve the plan: build, test, review, draft PR", "<card> [notes]",
                 lambda raw: build(dict(zip(("task_id", "text"), _split(raw))))),
    "cp-ship": ("Accept the change: final checks, PR ready for review", "<card> [last changes]",
                lambda raw: ship(dict(zip(("task_id", "text"), _split(raw))))),
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
    native.register(ctx)
    if not settings.load()["command_only"]:
        for name, schema in SCHEMAS.items():
            ctx.register_tool(name=name, toolset="cpipe", schema=schema,
                              handler=lambda args, _n=name, **kw: call(_n, args))
    if hasattr(ctx, "register_command"):
        from . import intents
        intents.register(ctx)
