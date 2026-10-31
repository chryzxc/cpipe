"""cpipe: a Hermes plugin made of skills. The model runs the delivery; this file only registers the run skill,
/cpipe, a reminder on messages sent while a run is going, and the run's rules pinned in the system prompt."""

import json
import os
import re
import sqlite3
from pathlib import Path

SKILL = Path(__file__).parent / "skills" / "run" / "SKILL.md"

START = ("Load the skill cpipe:run with skill_view (without skill_view, read {skill} with your file tool) and follow "
         "it to deliver this task: its files decide how the work is done, whichever profile this chat is. The "
         "Hermes root: {root}. Run it unattended to the report: this is my last message, so never stop to ask or wait.\n\n")

# A continue reuses the skill already in the chat: reloading it on every continue resends ~10k tokens per reply.
CONTINUE = ("Continue the cpipe run in this chat: load skill_view(name='cpipe:run', file_path='continue.md') and "
            "follow it. Load the skill cpipe:run itself with skill_view only "
            "when its text is no longer in this chat (without skill_view, read {skill} with your file tool). The "
            "Hermes root: {root}. Run it unattended to the report: this is my last message, so never stop to ask or "
            "wait.\n\n")
MARKS = (START[:30], CONTINUE[:30])  # how the chat's run messages start

# Budget tier -> reasoning effort of every profile run (passed as `hermes chat --reasoning`).
BUDGETS = {"unlimited": "max", "large": "xhigh", "medium": "high", "small": "medium"}

FLOW = "plan → tests → build → verify → QA + review → commit"

USAGE = f"""**cpipe** delivers a coding task unattended in this chat and reports back here.

{FLOW}

| Command | What it does |
|---|---|
| `/cpipe <task>` | start a run |
| `--parallel` | several issues, each in its own background session, reporting here |
| `--kanban` | role steps go to your profiles as cards on your Hermes kanban board |
| `--budget <tier>` | how hard your profiles reason: unlimited, large, medium or small |
| `/cpipe --continue` | pick up a run that stopped, at its first unfinished step (or just: continue) |
| `/cpipe --restart [<step>]` | redo the run from that step (default: explore) on its own roles |
| `/cpipe feedback <text>` | a problem you found after the report: recorded, reproduced and fixed |
| `/cpipe budget [<tier>\\|off]` | show or save the default budget in cpipe.yaml |
| `/cpipe learnings` | what cpipe has learned from your runs |

Example: `/cpipe --budget small fix the date filter on the orders page and open a draft pull request`"""

STARTED = """**cpipe** · {task}

{flow}

{team}

- **Budget:** {budget}
- **Learned:** {learned}{extra}

Runs on its own and reports here. Open questions are decided and listed in the report;
nothing is pushed unless you asked.

✅ done · ⏳ waiting, resumes on its own · ⚠️ problem, handled

Quiet with no ⏳? `/cpipe --continue`"""

LANES = "\n- **Lanes:** each issue in its own background session, up to 4 at once, each reporting here"
BOARD = "\n- **Board:** role steps run as cards on your Hermes kanban board"

RESUMED = """**cpipe** · resuming

Picks up at the first unfinished step; finished steps are not redone.{note}"""
RESTARTED = """**cpipe** · restarting from **{step}**

Redone on the run's own roles, in new sessions. Code it replaces is saved beside the run log first."""

FEEDBACK = """**cpipe** · recording your findings

Each one is reproduced, counted against the step that should have caught it, turned into a learning
and fixed as a follow-up."""

STEPS = ("general", "plan", "design", "build", "qa", "review")


def register(ctx):
    ctx.register_skill("run", SKILL)
    if hasattr(ctx, "register_command") and hasattr(ctx, "inject_message"):
        ctx.register_command("cpipe", lambda raw="": _start(ctx, raw),
                             description="Deliver a coding task unattended: plan, tests, build, review, report",
                             args_hint="[--parallel] [--kanban] [--budget <tier>] <task> | budget [<tier>] | --continue | --restart [<step>] | feedback <text> | learnings", argument_mode="text")
    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_llm_call", _steering)
        ctx.register_hook("pre_verify", _unfinished)
    if hasattr(ctx, "register_system_prompt_section"):
        ctx.register_system_prompt_section("cpipe.run", _pinned, max_chars=PINNED_MAX)


PINNED_MAX = 4000


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
        if not run or not run.is_dir():
            return ""
        text = (SKILL.parent / "contract.md").read_text().replace("<skill dir>", str(SKILL.parent))
        sections = dict(re.findall(r"(?ms)^## (\w+)\n(.*?)(?=^## |\Z)", text))
        own = sections["Step" if os.environ.get("CPIPE_STEP") else "Run"].strip()
        parts = [own + "\n" + sections["Both"].strip(), f"Run folder: {run}"]
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
            "answer.")
# The run's own lines (skills/run/SKILL.md, Status lines) and the report's status lines (steps/report.md).
STEP_LINE = re.compile(r"^(?:[✅⏳⚠❌—]\ufe0f? )?\d{1,2}/11 |^Resuming at \d{1,2}/11 ", re.M)
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


def _steering(user_message=None, conversation_history=None, **_):
    """Context for a user message that arrives while a run is going (no flow control: the skill decides)."""
    message = _text(user_message).lstrip()
    if not message or message.startswith((*MARKS, "/cpipe")):
        return None
    return {"context": STEERING} if _running(conversation_history) else None


UNFINISHED = ("[cpipe] The run's gate still has work, so this turn cannot end here: {verdict}\n"
              "A turn ends only on a ⏳ wait line or the report; a choice between fixes is never the user's while the "
              "gate has work (cpipe:run, Limits). Do the next item now, and finish the goal.")


def _unfinished(session_id="", final_response="", **_):
    """pre_verify: the chat is about to end its turn. While `cpipe.py gate` says FIX, only a wait or pause line
    (⏳) may end it; anything else (a question, a "not ready" summary) gets the gate's own verdict back."""
    try:
        run = None if os.environ.get("CPIPE_STEP") else _run_dir({"session_id": session_id})
        if not run or not run.is_dir() or _text(final_response).lstrip().startswith("⏳"):
            return None
        import importlib.util
        spec = importlib.util.spec_from_file_location("cpipe_facts", SKILL.parent / "bin" / "cpipe.py")
        facts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(facts)
        verdict = facts.gate(run, str(session_id or "") or None)
        if not verdict.startswith("FIX"):
            return None
        return {"action": "continue", "message": UNFINISHED.format(verdict=_short(verdict, 1500))}
    except Exception:  # a hook never breaks a session
        return None


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
    return "\n".join(["| Step | Runs on |", "|---|---|", *(f"| {s} | {p} |" for s, p in _roles())])


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
        return "each profile's own setting · `/cpipe budget <tier>` sets one"
    return f"{tier} · every profile reasons at {BUDGETS[tier]}"


def _budget(arg):
    tiers = ", ".join(BUDGETS)
    if not arg:
        return f"**cpipe budget** · {_budget_line(_saved_budget(), False)}\n\nSet it: `/cpipe budget <{tiers}|off>`"
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
    return f"{n} rule{'s' * (n != 1)} from past runs, added to this one · `/cpipe learnings`"


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
                     "have seen (`/cpipe feedback <what you found>`).")


def _flags(task):
    """Leading --parallel, --kanban and --budget <tier>, in any order; returns (flags, budget, rest, error)."""
    flags, budget, words = set(), None, task.split()
    while words and words[0] in ("--parallel", "--kanban", "--budget"):
        flag = words.pop(0)
        if flag != "--budget":
            flags.add(flag)
        elif words and words[0] in BUDGETS:
            budget = words.pop(0)
        else:
            return flags, budget, "", "--budget needs one of: " + ", ".join(BUDGETS)
    return flags, budget, " ".join(words), None


def _short(text, n=100):
    return text if len(text) <= n else text[:n - 1] + "…"


# Text input often turns a typed "--" into a dash: "—restart" is still the flag.
DASHED = re.compile(r"(?<!\S)[—–](?=(parallel|kanban|budget|continue|restart)\b)")


def _start(ctx, raw):
    task = DASHED.sub("--", raw.strip())
    first, _, rest = task.partition(" ")
    if first.lower() == "learnings" and not rest.strip():
        return _learnings()
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
        if ctx.inject_message((CONTINUE if resume else START).format(skill=SKILL, root=_root()) + task, role="user",
                              session_key=key):
            if first.lower() == "feedback":
                return FEEDBACK
            if first.lower() == "--restart":
                return RESTARTED.format(step=rest.split()[0] if rest.split() else "explore")
            if resume:
                return RESUMED.format(note=f'\nYour note: "{_short(rest)}"' if rest else "")
            return STARTED.format(task=_short(brief), flow=FLOW, team=_team(),
                                  budget=_budget_line(budget or _saved_budget(), kanban), learned=_learned(),
                                  extra=(LANES if "--parallel" in flags else "") + (BOARD if kanban else ""))
        reason = "the chat did not accept the message"
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
    return (f"**cpipe** · could not start the run here ({reason})\n\n"
            "Type `/cpipe:run` followed by the task instead: it runs the same flow.")
