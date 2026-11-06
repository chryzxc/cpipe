"""cpipe: a Hermes plugin made of skills. The model runs the delivery; this file only registers the run skill,
/cpipe, a reminder on messages sent while a run is going, and the run's rules pinned in the system prompt."""

import json
import os
import re
import sqlite3
import time
from pathlib import Path

SKILL = Path(__file__).parent / "skills" / "run" / "SKILL.md"

# The user's standing answer to "may I go beyond the named fix?": a model asks it however the skill words its rules.
SCOPE = ("Whatever the goal needs inside the worktree is authorized, a larger change or another design than the fix "
         "the task names included: never ask me for scope. Only credentials, destructive actions, what leaves this "
         "machine beyond what I asked for, and the approvals Hermes asks are mine.")
START = ("Load the skill cpipe:run with skill_view (without skill_view, read {skill} with your file tool) and follow "
         "it to deliver this task: its files decide how the work is done, whichever profile this chat is. The "
         "Hermes root: {root}. Run it to the report without stopping: ask me only spec questions (cpipe:run, spec.md), "
         "keep working while they are open, and wait on them only when nothing else is left. " + SCOPE + "\n\n")
# --spec: the spec is worked out with the user first, so the unattended wording above must not reach the planner.
SPEC_START = START.replace(
    "Run it to the report without stopping: ask me only spec questions (cpipe:run, spec.md), keep working while "
    "they are open, and wait on them only when nothing else is left.",
    "Work out the spec with me first (cpipe:run, spec.md, --spec): nothing is designed, planned or built until I "
    "agree to it, and every reply of the planner comes to me, questions or not. Once I agree, run it to the report "
    "without stopping.")
assert SPEC_START != START

# A continue reuses the skill already in the chat: reloading it on every continue resends ~10k tokens per reply.
CONTINUE = ("Continue the cpipe run in this chat: load skill_view(name='cpipe:run', file_path='continue.md') and "
            "follow it. Load the skill cpipe:run itself with skill_view again unless this chat has its version "
            "{version} (without skill_view, read {skill} with your file tool). The "
            "Hermes root: {root}. Run it to the report without stopping: ask me only spec questions (cpipe:run, spec.md), "
            "keep working while they are open, and wait on them only when nothing else is left. " + SCOPE + "{verdict}\n\n")
MARKS = (START[:30], CONTINUE[:30])  # how the chat's run messages start

# Budget tier -> reasoning effort of every profile run (passed as `hermes chat --reasoning`).
BUDGETS = {"unlimited": "max", "large": "xhigh", "medium": "high", "small": "medium"}

FLOW = "plan → tests → build → verify → QA + review → commit"

USAGE = f"""**cpipe** delivers a coding task unattended in this chat and reports back here.

{FLOW}

| Command | What it does |
|---|---|
| `/cpipe <task>` | start a run |
| `/cpipe-spec <task>` | first work out the spec with you (choices to pick from), then it delivers on its own |
| `/cpipe-parallel <issues>` | several issues, each in its own background session, reporting here |
| `/cpipe-kanban <task>` | role steps go to your profiles as cards on your Hermes kanban board |
| `/cpipe-continue [<note>]` | pick up a run that stopped, at its first unfinished step (or just: continue) |
| `/cpipe-restart [<step>]` | redo the run from that step (default: explore) on its own roles |
| `/cpipe-feedback <text>` | a problem you found after the report: recorded, reproduced and fixed |
| `/cpipe-stop` | end the run in this chat now: its steps stop, and your next messages are plain chat work |
| `/cpipe-budget [<tier>\\|off]` | how hard your profiles reason (unlimited, large, medium, small): show or save it |
| `/cpipe-learnings` | what cpipe has learned from your runs |
| `/cpipe-rules [<what to change>]` | show your own rules for cpipe runs, or change them in words |

Example: `/cpipe-spec fix the date filter on the orders page and open a draft pull request`"""

STARTED = """**cpipe** · {task}

{flow}

{team}

- **Budget:** {budget}
- **Your rules:** {rules}
- **Learned:** {learned}{extra}

Runs on its own and reports here. Open questions are decided and listed in the report; one only you can
answer is asked as ❓ while the work goes on. Nothing is pushed unless you asked.

✅ done · ⏳ waiting, resumes on its own · ❓ your answer needed · ⚠️ problem, handled

Quiet with no ⏳? `/cpipe-continue`"""

LANES = "\n- **Lanes:** each issue in its own background session, up to 4 at once, each reporting here"
SPEC = "\n- **Spec:** first worked out with your plan profile, here in this chat; then the run builds on its own"
BOARD = "\n- **Board:** role steps run as cards on your Hermes kanban board"

RESUMED = """**cpipe** · resuming

Picks up at the first unfinished step; finished steps are not redone.{note}"""
RESTARTED = """**cpipe** · restarting from **{step}**

Redone on the run's own roles, in new sessions. Code it replaces is saved beside the run log first."""

STOPPED = """**cpipe** · run stopped

Its background steps are stopped and nothing resumes it. Your next messages are plain chat work; `/cpipe-continue`
picks the run up again."""
NOT_RUNNING = "**cpipe** · no run is going in this chat"
# Sent to the chat so the model stops what the run started; the registration is already gone (_stop).
STOP = ("[cpipe] The user ended the cpipe run in this chat with /cpipe-stop, and it is closed. Kill the background "
        "processes it started (its step, tick and limit commands) with your process tool, add a `stopped by the "
        "user` row to its run log, and reply in one line. Do not continue, resume or report the run: later "
        "messages are the user's own work (cpipe:run, The user ends the run).")

FEEDBACK = """**cpipe** · recording your findings

Each one is reproduced, counted against the step that should have caught it, turned into a learning
and fixed as a follow-up."""

# Each mode is its own command, so the composer lists it; it reaches _start as the word the skill reads.
COMMANDS = {
    "spec": ("--spec", "<task>", "Work out the spec with you first, then deliver it unattended"),
    "parallel": ("--parallel", "<issues>", "Deliver several issues at once, each in its own background session"),
    "kanban": ("--kanban", "<task>", "Deliver a task with role steps as cards on your kanban board"),
    "continue": ("--continue", "[<note>]", "Pick up this chat's stopped cpipe run at its first unfinished step"),
    "restart": ("--restart", "[<step>]", "Redo this chat's cpipe run from a step (default: explore)"),
    "feedback": ("feedback", "<what you found>", "Report a problem found after a cpipe run: reproduced and fixed"),
    "stop": ("stop", "", "End the cpipe run in this chat now"),
    "budget": ("budget", "[unlimited|large|medium|small|off]", "Show or save how hard cpipe's profiles reason"),
    "learnings": ("learnings", "", "What cpipe has learned from your runs"),
    "rules": ("--rules", "[<what to change>]", "Show your rules for cpipe runs, or change them in words"),
}

STEPS = ("general", "plan", "design", "build", "qa", "review")


def register(ctx):
    ctx.register_skill("run", SKILL)
    if hasattr(ctx, "register_command") and hasattr(ctx, "inject_message"):
        ctx.register_command("cpipe", lambda raw="": _start(ctx, raw),
                             description="Deliver a coding task unattended: plan, tests, build, review, report",
                             args_hint="<task>", argument_mode="text")
        for name, (word, hint, description) in COMMANDS.items():
            ctx.register_command(f"cpipe-{name}", _mode(ctx, word),
                                 description=description, args_hint=hint, argument_mode="text" if hint else None)
    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_llm_call", _steering)
        ctx.register_hook("pre_verify", _unfinished)
        ctx.register_hook("post_llm_call", lambda **kw: _watch(ctx, **kw))
    if hasattr(ctx, "register_system_prompt_section"):
        ctx.register_system_prompt_section("cpipe.run", _pinned, max_chars=PINNED_MAX)


def _mode(ctx, word):
    return lambda raw="": _start(ctx, f"{word} {raw}".strip())


PINNED_MAX = 4000
PENDING_FOR = 86400  # ponytail: a run never registered stops being nudged after a day
UNREGISTERED = ("FIX: no run folder is registered for this chat, so nothing checks the run: set it up "
                "(steps/setup.md) and register it, `python3 {skill}/bin/cpipe.py active <run dir>`, then go on.")


def _lineage(session_id, hops=50):
    """This session and the ones it was compacted from: a compaction continues the chat under a new id."""
    ids, sid = [], session_id
    try:
        db = sqlite3.connect(f"file:{_home() / 'state.db'}?mode=ro", uri=True, timeout=1)
        try:
            while sid and sid not in ids and len(ids) < hops:
                ids.append(sid)
                row = db.execute("SELECT parent_session_id FROM sessions WHERE id = ?", (sid,)).fetchone()
                sid = row[0] if row else None
        finally:
            db.close()
    except sqlite3.Error:
        pass
    return ids or ([session_id] if session_id else [])


def _run_dir(info):
    """The run this session works on: CPIPE_RUN for a step's own session, the run a --parallel lane wrote to its
    lane folder (bin/lane.sh), else the chat's registered run."""
    if os.environ.get("CPIPE_RUN"):
        return Path(os.environ["CPIPE_RUN"])
    if os.environ.get("CPIPE_LANE"):
        try:
            return Path((Path(os.environ["CPIPE_LANE"]) / "run").read_text().strip())
        except OSError:
            return None
    active = _root() / "cpipe" / "active"
    if not active.is_dir():
        return None
    for sid in _lineage(str(info.get("session_id") or "")):
        try:
            return Path(json.loads((active / f"{sid}.json").read_text())["run_dir"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def _pending(session_id):
    """True when /cpipe started a run in this chat and setup has not registered its run dir yet (_start)."""
    for sid in _lineage(str(session_id or "")):
        try:
            data = json.loads((_root() / "cpipe" / "active" / f"{sid}.json").read_text())
        except (OSError, ValueError):
            continue
        return "run_dir" not in data and time.time() - data.get("pending", 0) < PENDING_FOR
    return False


def _instructions(now):
    """now.md's `## Instructions` section: the user's words, which win over every default."""
    try:
        text = now.read_text()
    except OSError:
        return ""
    found = re.search(r"(?ms)^## Instructions\b.*?(?=^## |\Z)", text)
    return found.group(0).strip() if found else ""


def _pinned(info):
    """The run's rules and open items, in the system prompt so a compaction cannot drop them (no flow control)."""
    try:
        run = _run_dir(info)
        pending = (not run or not run.is_dir()) and _pending(info.get("session_id"))
        if not pending and (not run or not run.is_dir()):
            return ""
        text = (SKILL.parent / "contract.md").read_text().replace("<skill dir>", str(SKILL.parent))
        sections = dict(re.findall(r"(?ms)^## (\w+)\n(.*?)(?=^## |\Z)", text))
        own = sections["Step" if os.environ.get("CPIPE_STEP") else "Run"].strip()
        parts = [own + "\n" + sections["Both"].strip()]
        if pending:
            return "\n\n".join(parts + [UNREGISTERED.format(skill=SKILL.parent)])
        parts.append(f"Run folder: {run}")
        rules = _rules(_run_git(run))
        if rules:
            parts.append(RULES_PINNED + "\n" + "\n".join(rules))
        told = _instructions(run / "now.md")
        if told:
            parts.append(told)
        try:
            items = [i for i in json.loads((run / "track.json").read_text()) if i.get("status") == "open"]
        except (OSError, ValueError):
            items = []
        if items:
            parts.append("Open in track.json:\n" + "\n".join(f"- {i['id']} {i['text']}" for i in items))
        text = "\n\n".join(parts)
        if len(text) > PINNED_MAX:
            text = text[:PINNED_MAX - 40].rstrip() + "\n… (the rest is in the run folder)"
        return text
    except Exception:  # a prompt section never breaks a session
        return ""


STEERING = ("[cpipe] A cpipe run is going in this chat and this message arrived mid-run: it is steering, not a new "
            "task (cpipe:run, A message from the user mid-run). Do what it asks as part of the run, answer it in one "
            "line in the same reply as the run's next tool call, and continue the run. Never end the turn on the "
            "answer. An answer to a ❓ question goes into spec.md and closes its item; while spec.md says drafting, the "
            "message is the user's reply to the planner: pass it on word for word (cpipe:run, spec.md). A message that "
            "tells you to stop or cancel the run, or not to use cpipe, is not steering: it ends the run now "
            "(cpipe:run, The user ends the run).")
# The run's own lines (skills/run/SKILL.md, Status lines) and the report's status lines (steps/report.md).
STEP_LINE = re.compile(r"^(?:[✅⏳❓⚠❌—]\ufe0f? )?\d{1,2}/11 |^Resuming at \d{1,2}/11 ", re.M)
REPORT_LINE = re.compile(r"^(?:✅ Done|⚠\ufe0f? Done except|✅ Approved|⚠\ufe0f? Changes needed)", re.M)


def _text(content):
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


def _running(history):
    """True when the newest cpipe marker in the chat is a start or a step line, not the report."""
    for msg in reversed(history or []):
        if not isinstance(msg, dict):
            continue
        text = _text(msg.get("content"))
        if msg.get("role") == "user" and text.startswith(MARKS):
            return True
        if msg.get("role") == "assistant":
            if REPORT_LINE.search(text):
                return False
            if STEP_LINE.search(text):
                return True
    return False


def _steering(user_message=None, conversation_history=None, session_id="", **_):
    """Context for a user message that arrives while a run is going (no flow control: the skill decides). A run
    is going only while it is registered: its report closes it, and later messages are the user's own work."""
    message = _text(user_message).lstrip()
    if not message or message.startswith((*MARKS, "/cpipe", "[cpipe")) or not _running(conversation_history):
        return None
    run = _run_dir({"session_id": session_id})
    return {"context": STEERING} if (run and run.is_dir()) or _pending(session_id) else None


UNFINISHED = ("[cpipe] The run's gate still has work, so this turn cannot end here: {verdict}\n"
              "A turn ends only on a ⏳ wait line or the report; a ❓ question goes with the next tool call; a choice between fixes is never the user's while the "
              "gate has work (cpipe:run, Limits). Do the next item now, and finish the goal.")


IDLE_WAIT = ("[cpipe] This turn ends on a ⏳ wait, but nothing this chat started is running, so nothing will "
             "wake the run: start the work it waits on in the background with a completion notification, or do "
             "the next item now.")


def _waiting(session_id):
    """True when this chat has a background process running: its completion notification starts the next turn
    (the same snapshot Hermes' own goal loop waits on)."""
    try:
        from hermes_cli.goals import gather_background_processes
    except ImportError:  # a host without the snapshot: take the ⏳ line at its word
        return True
    return bool(gather_background_processes(owner_task_id=session_id or None))


def _gate(run, session_id):
    """`cpipe.py gate` on the run: the facts decide what is left, not the chat's last reply."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("cpipe_facts", SKILL.parent / "bin" / "cpipe.py")
    facts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(facts)
    return facts.gate(run, str(session_id or "") or None)


def _version():
    return re.search(r"(?m)^version: (\S+)", SKILL.read_text()).group(1)


def _verdict(session_id):
    """The run's gate for a continue message, so the turn starts from the facts."""
    try:
        run = _run_dir({"session_id": session_id})
        return f" The run's gate now: {_short(_gate(run, session_id), 1500)}" if run and run.is_dir() else ""
    except Exception:  # no run yet, or no facts: the continue reads them itself
        return ""


def _unfinished(session_id="", final_response="", **_):
    """pre_verify: the chat is about to end its turn. While `cpipe.py gate` says FIX, only a wait or pause line
    (⏳) with work running may end it (ASK, only the user's answers left, ends on its ❓ lines); anything else (a question, a "not ready" summary, a wait on nothing)
    gets the gate's own verdict back."""
    try:
        run = None if os.environ.get("CPIPE_STEP") else _run_dir({"session_id": session_id})
        if not run or not run.is_dir():
            return None
        if _text(final_response).lstrip().startswith("⏳"):
            return None if _waiting(session_id) else {"action": "continue", "message": IDLE_WAIT}
        verdict = _gate(run, session_id)
        if not verdict.startswith("FIX"):
            return None
        return {"action": "continue", "message": UNFINISHED.format(verdict=_short(verdict, 1500))}
    except Exception:  # a hook never breaks a session
        return None


WATCH = ("[cpipe watcher] Your last reply ended the turn, but the run is not finished and nothing it started is "
         "running to wake it. The gate: {verdict}\n{ask}\nCheck that reply against the goal and spec.md: was "
         "what it did right, and what is the next item? Do it now and go on to the report (cpipe:run).")
ASKS = {
    "paused": "The turn limit paused it: go on from its Next line.",
    "wait": "It ended on a ⏳ wait with nothing running: start what it waits on in the background, or do the work.",
    "ask": "Only your questions are left: end the turn on the ❓ waiting-on-your-answer lines (spec.md).",
    "user": "Only the user's own items are left: end the turn on their ⚠️ lines, or finish what you still can.",
    "report": "Every item is done: write the report now (steps/report.md).",
    "fix": "Do the next item.",
    "stuck": "This is nudge {n} with no change in the gate or the checklist: the same approach is not working. "
             "Change it (cpipe:run, Limits): re-read the failure, re-plan that item, or take another design.",
}
STUCK_AT, GIVE_UP = 3, 8  # ponytail: identical nudges before "change approach" / before the watcher lets go
HALT = re.compile(r"\b(stop|pause|hold|cancel|abort)\b", re.I)


def _nudge(reply, verdict):
    """What the watcher asks of the next turn, or None when this turn may end where it did."""
    if REPORT_LINE.search(reply):
        return None
    kind = verdict.split(":", 1)[0]
    if reply.lstrip().startswith("⏳ Paused"):
        return "paused"
    if kind in ("SPEC", "ASK"):
        return None if "❓" in reply else "ask"
    if kind == "NEEDS-USER":
        return None if "⚠" in reply else "user"
    if kind == "PASS":
        return "report"
    return "wait" if "⏳" in reply else "fix"


def _progress(run, verdict, ask, seen=None):
    """How many nudges in a row found the run as it is now (gate and checklist unchanged), this one included."""
    import hashlib
    try:
        track = (run / "track.md").read_text() if run else ""
    except OSError:
        track = ""
    mark = hashlib.sha1(f"{verdict}\n{track}\n{ask}".encode()).hexdigest()
    seen = seen or run / "watch.txt"
    try:
        last, n = seen.read_text().split()
        n = int(n) + 1 if last == mark else 1
    except (OSError, ValueError):
        n = 1
    seen.write_text(f"{mark} {n}")
    return n


def _watch(ctx, session_id="", user_message="", assistant_response="", conversation_history=None, **_):
    """post_llm_call: the run's watcher. After each turn of a run's chat it reads the gate; when the turn ended
    with the run unfinished and nothing running to wake it, it queues the next turn, the way Hermes' goal loop
    continues one, with the gate's facts and a check of the last reply. A turn the user stopped is left alone."""
    try:
        if os.environ.get("CPIPE_STEP") or os.environ.get("CPIPE_LANE"):
            return  # a role's session ends on its step; a stopped lane is resumed by its dispatcher
        message = _text(user_message)
        if not message.startswith(MARKS) and not message.startswith("[cpipe") and HALT.search(message[:200]):
            return
        if not _running(conversation_history):
            return
        run = _run_dir({"session_id": session_id})
        if not run or not run.is_dir():
            run = None
            if not _pending(session_id):
                return
        if _waiting(session_id):
            return
        verdict = _gate(run, session_id) if run else UNREGISTERED.format(skill=SKILL.parent)
        ask = _nudge(_text(assistant_response), verdict)
        if not ask:
            return
        n = _progress(run, verdict, ask, None if run else _root() / "cpipe" / "active" / f"{session_id}.watch")
        if n > GIVE_UP:
            return
        hint = ASKS["stuck"].format(n=n) if n >= STUCK_AT else ASKS[ask]
        from gateway.session_context import get_session_env
        key = get_session_env("HERMES_SESSION_KEY") or get_session_env("HERMES_SESSION_ID") or None
        ctx.inject_message(WATCH.format(verdict=_short(verdict, 1500), ask=hint), role="user", session_key=key)
    except Exception:  # a hook never breaks a session
        return


def _home():
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _root():
    """The Hermes root every profile shares: two levels above a profile's folder."""
    home = _home()
    return home.parent.parent if home.parent.name == "profiles" else home


def _config():
    """This profile's own cpipe.yaml when it has one, else the one in the Hermes root."""
    own = _home() / "cpipe.yaml"
    return own if own.exists() else _root() / "cpipe.yaml"


def _roles():
    """(step, who runs it) rows for the start message, in flow order."""
    try:
        roles = dict(re.findall(r"(?m)^[ \t]+(\w+):[ \t]*([\w.-]+)", _config().read_text()))
    except OSError:
        roles = {}
    if not roles:
        return [("build, QA, review", "this chat's model, each in its own session (no roles in cpipe.yaml)")]
    if "plan" in roles or "build" in roles:  # one reader explores (skills/run/roles.md)
        roles.pop("explore", None)
    order = ("explore", "plan", "design", "build", "qa", "review", "security")
    rows = sorted(roles.items(), key=lambda r: order.index(r[0]) if r[0] in order else len(order))
    return rows + [("everything else", "this chat's model")]


def _team():
    rows = (f"| {s} | {p if ' ' in p else '@' + p} |" for s, p in _roles())  # a profile shows as its @bot
    return "\n".join(["| Step | Runs on |", "|---|---|", *rows])


def _saved_budget():
    try:
        found = re.search(r"(?m)^budget:[ \t]*(\w+)", _config().read_text())
    except OSError:
        return None
    return found.group(1) if found and found.group(1) in BUDGETS else None


def _budget_line(tier, kanban):
    if kanban:
        return "not applied on the board (cards take no reasoning setting)"
    if not tier:
        return "each profile's own setting · `/cpipe-budget <tier>` sets one"
    return f"{tier} · every profile reasons at {BUDGETS[tier]}"


def _budget(arg):
    tiers = ", ".join(BUDGETS)
    if not arg:
        return f"**cpipe budget** · {_budget_line(_saved_budget(), False)}\n\nSet it: `/cpipe-budget <{tiers}|off>`"
    path = _config()
    try:
        text = path.read_text()
    except FileNotFoundError:
        text = ""
    except OSError as exc:
        return f"cpipe could not read {path}: {exc}"
    text = re.sub(r"(?m)^budget:.*\n?", "", text)
    if arg != "off":
        text += ("" if not text or text.endswith("\n") else "\n") + f"budget: {arg}\n"
    try:
        path.write_text(text)
    except OSError as exc:
        return f"cpipe could not write {path}: {exc}"
    return (f"**cpipe budget** · {_budget_line(arg if arg != 'off' else None, False)}\n\n"
            "Saved in cpipe.yaml for every run.")


def _learned_lines(name):
    path = _root() / "cpipe" / "learnings" / f"{name}.md"
    return [l for l in path.read_text().splitlines() if l.startswith("- ")] if path.exists() else []


def _learned():
    n = sum(len(_learned_lines(name)) for name in STEPS)
    if not n:
        return "nothing yet; every miss becomes a rule for the next run"
    return f"{n} rule{'s' * (n != 1)} from past runs, added to this one · `/cpipe-learnings`"


def _learnings():
    """The learnings and, per step, the misses that reached the user in the last 20 runs (skills/run/SKILL.md)."""
    import json
    base = _root() / "cpipe"
    out = []
    for name in (*STEPS, "retired"):
        lines = _learned_lines(name)
        if lines:
            out += [f"**{name}** ({len(lines)})", *lines, ""]
    try:
        rows = [json.loads(l) for l in (base / "history.jsonl").read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        rows = []
    runs = [r for r in rows if r.get("type") == "run"][-20:]
    since = runs[0].get("date", "") if runs else ""
    misses = {}
    for r in rows:
        if r.get("type") == "miss" and r.get("found_by") == "user" and r.get("date", "") >= since:
            misses[r.get("step", "?")] = misses.get(r.get("step", "?"), 0) + 1
    if runs:
        out.append(f"Last {len(runs)} runs, problems you found after the report: "
                   + (", ".join(f"{s} {n}" for s, n in misses.items()) or "none"))
    return ("**cpipe learnings**\n\n" + "\n".join(out).strip()) if out else ( "cpipe has no learnings yet. They are written when review, QA or you catch something a run should "
                     "have seen (`/cpipe-feedback <what you found>`).")


RULES_PINNED = ("The user's own rules for cpipe runs: their standing instructions, held like now.md's. Where two "
                "differ, the project's win over the profile's, the profile's over everywhere's, and this task's "
                "instructions over all of them (cpipe:run, The user's rules).")


def _run_git(run):
    """The shared git folder of a run dir (<git dir>/cpipe; a worktree's git dir is <common>/worktrees/<name>)."""
    git = Path(run).parent
    return git.parent.parent if git.parent.name == "worktrees" else git


def _rule_files(project=None):
    """(scope, rules.md) from the widest to the most specific, like cpipe.yaml: the Hermes root, this profile's own
    folder, then the project's shared git folder."""
    files = [("everywhere", _root() / "cpipe" / "rules.md")]
    if _home() != _root():
        files.append(("this profile", _home() / "cpipe" / "rules.md"))
    if project:
        files.append(("this project", Path(project) / "cpipe" / "rules.md"))
    return files


def _read_rules(path):
    try:
        return [l for l in path.read_text().splitlines() if l.startswith("- ")]
    except OSError:
        return []


def _rules(project=None):
    """The user's rules for a run, each `- ` line tagged with its scope, widest first."""
    return [f"{l} ({scope})" for scope, path in _rule_files(project) for l in _read_rules(path)]


def _rules_line():
    n = len(_rules())
    if not n:
        return "none · `/cpipe-rules <a rule>` sets one"
    return f"{n} standing rule{'s' * (n != 1)}, plus any for this project · `/cpipe-rules`"


# `/cpipe-rules <what to change>`: the chat's model edits the files; which one comes from the user's words.
RULES_EDIT = ("Edit my cpipe rules as I ask below, then show the rules files you changed. Do not start a run. "
              "They are cpipe's own standing rules, one `- ` line each in my words, at three levels like cpipe.yaml: "
              "{root} for every run, {profile} for one profile's sessions, and `$(git -C <repo> rev-parse "
              "--path-format=absolute --git-common-dir)/cpipe/rules.md` for one repository's runs (never committed). "
              "Pick the level from my words: a repository or project I name, or this chat's when I say this project; "
              "a profile I name; else every run. Merge a rule into one that says the same thing rather than adding a "
              "near-duplicate.\n\n")


def _rules_cmd(ctx, arg):
    """`/cpipe-rules`: show the rules; `/cpipe-rules <what to change>`: the chat's model edits them."""
    if arg:
        from gateway.session_context import get_session_env
        key = get_session_env("HERMES_SESSION_KEY") or get_session_env("HERMES_SESSION_ID") or None
        text = RULES_EDIT.format(root=_root() / "cpipe" / "rules.md",
                                 profile=_root() / "profiles" / "<name>" / "cpipe" / "rules.md")
        if ctx.inject_message(text + arg, role="user", session_key=key):
            return "**cpipe rules** · updating them as you asked"
        return "cpipe could not pass that on in this chat: edit the rules files directly.\n\n" + _rules_cmd(ctx, "")
    out = [f"**{s}**\n" + _list(_read_rules(p), p) for s, p in _rule_files()]
    return ("**cpipe rules** · your own rules for cpipe runs only, followed like your instructions; the most "
            "specific wins, and a task's own instructions win over all\n\n" + "\n\n".join(out)
            + "\n\nA repository's own rules are in its `.git/cpipe/rules.md`. Change any of them in words: "
            "`/cpipe-rules commit and push after each commit, only in this project`")


def _list(rules, path):
    if not rules:
        return f"none · {path}"
    return "\n".join(f"{i}. {r[2:]}" for i, r in enumerate(rules, 1)) + f"\n{path}"


def _flags(task):
    """Leading --parallel, --kanban, --spec and --budget <tier>, in any order; returns (flags, budget, rest, error)."""
    flags, budget, words = set(), None, task.split()
    while words and words[0] in ("--parallel", "--kanban", "--spec", "--budget"):
        flag = words.pop(0)
        if flag != "--budget":
            flags.add(flag)
        elif words and words[0] in BUDGETS:
            budget = words.pop(0)
        else:
            return flags, budget, "", "--budget needs one of: " + ", ".join(BUDGETS)
    return flags, budget, " ".join(words), None


def _register_pending(sid):
    """The run is this chat's from its first message: until setup registers its run dir (`cpipe.py active`), the
    pinned rules and the watcher hold it to that, so a run that skips it is never left unchecked."""
    path = _root() / "cpipe" / "active" / f"{sid}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pending": int(time.time())}) + "\n")
        path.with_suffix(".watch").unlink(missing_ok=True)
    except OSError:
        pass


def _stop(ctx):
    """/cpipe-stop: close this chat's run (and its compacted parents' records) at once, so the steering, pinned rules
    and gate let go before the model even answers; then tell the model to stop what the run started."""
    from gateway.session_context import get_session_env
    sid = get_session_env("HERMES_SESSION_ID") or ""
    active = _root() / "cpipe" / "active"
    records = [active / f"{each}{suffix}" for each in _lineage(sid) for suffix in (".json", ".watch")]
    if not any(path.exists() for path in records):
        return NOT_RUNNING
    for path in records:
        path.unlink(missing_ok=True)
    key = get_session_env("HERMES_SESSION_KEY") or sid or None
    ctx.inject_message(STOP, role="user", session_key=key)
    return STOPPED


def _short(text, n=100):
    return text if len(text) <= n else text[:n - 1] + "…"


# Text input often turns a typed "--" into a dash: "—restart" is still the flag.
DASHED = re.compile(r"(?<!\S)[—–](?=(parallel|kanban|spec|budget|continue|restart|rules)\b)")


def _start(ctx, raw):
    task = DASHED.sub("--", raw.strip())
    first, _, rest = task.partition(" ")
    if first.lower() == "learnings" and not rest.strip():
        return _learnings()
    if first.lower() == "--rules":
        return _rules_cmd(ctx, rest.strip())
    if first.lower() == "stop" and not rest.strip():
        return _stop(ctx)
    if first.lower() == "budget" and (not rest.strip() or rest.strip().lower() in (*BUDGETS, "off")):
        return _budget(rest.strip().lower())
    flags, budget, brief, error = _flags(task)
    kanban = "--kanban" in flags
    if error:
        return error + "\n\n" + USAGE
    if not brief:
        return USAGE
    from gateway.session_context import get_session_env
    key = get_session_env("HERMES_SESSION_KEY") or get_session_env("HERMES_SESSION_ID") or None
    try:
        first, _, rest = brief.partition(" ")
        resume = first.lower().rstrip(".!") in ("continue", "resume", "--continue", "--restart")
        sid = get_session_env("HERMES_SESSION_ID") or ""
        text = (CONTINUE.format(skill=SKILL, root=_root(), version=_version(), verdict=_verdict(sid)) if resume
                else (SPEC_START if "--spec" in flags else START).format(skill=SKILL, root=_root()))
        if ctx.inject_message(text + task, role="user",
                              session_key=key):
            if not resume and first.lower() != "feedback" and "--parallel" not in flags and sid:
                _register_pending(sid)
            if first.lower() == "feedback":
                return FEEDBACK
            if first.lower() == "--restart":
                return RESTARTED.format(step=rest.split()[0] if rest.split() else "explore")
            if resume:
                return RESUMED.format(note=f'\nYour note: "{_short(rest)}"' if rest else "")
            return STARTED.format(task=_short(brief), flow=FLOW, team=_team(),
                                  budget=_budget_line(budget or _saved_budget(), kanban), learned=_learned(),
                                  rules=_rules_line(),
                                  extra=(LANES if "--parallel" in flags else "") + (SPEC if "--spec" in flags else "")
                                  + (BOARD if kanban else ""))
        reason = "the chat did not accept the message"
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
    return (f"**cpipe** · could not start the run here ({reason})\n\n"
            "Type `/cpipe:run` followed by the task instead: it runs the same flow.")
