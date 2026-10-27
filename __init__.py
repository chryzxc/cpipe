"""cpipe: a Hermes plugin made of skills. The model runs the delivery; this file only registers the run skill, /cpipe
and a reminder on messages sent while a run is going."""

import os
import re
from pathlib import Path

SKILL = Path(__file__).parent / "skills" / "run" / "SKILL.md"

START = ("Load the skill cpipe:run with skill_view (without skill_view, read {skill} with your file tool) and follow "
         "it to deliver this task: its files decide how the work is done, whichever profile this chat is. The "
         "Hermes root: {root}. Run it unattended to the report: this is my last message, so never stop to ask or wait.\n\n")

# Budget tier -> reasoning effort of every profile run (passed as `hermes chat --reasoning`).
BUDGETS = {"unlimited": "max", "large": "xhigh", "medium": "high", "small": "medium"}

USAGE = """Usage: /cpipe [--kanban] [--budget <tier>] <task>
Delivers a coding task unattended in this chat and reports back here.
Steps: plan, tests first, build, verify with the project's checks, QA and independent review, commit.
  --kanban          hand the role steps to your profiles as cards on your Hermes kanban board
  --budget <tier>   how hard your profiles reason on this run: unlimited, large, medium or small
  /cpipe budget [<tier>|off]   show or save the default budget in cpipe.yaml
  /cpipe continue   pick up a run that stopped, at its first unfinished step
  /cpipe feedback <what you found>   a problem you found after the report: recorded, reproduced and fixed
  /cpipe learnings  what cpipe has learned from your runs
Example: /cpipe --budget small fix the date filter on the orders page and open a draft pull request"""

STARTED = """cpipe · {task}

  Flow     plan → tests → build → verify → QA + review → commit
  Team     {roles}
  Budget   {budget}
  Learned  {learned}

Works on its own and reports here: open questions are decided and listed in the report,
nothing is pushed unless you asked.
✅ done · ⏳ waiting, resumes on its own · ⚠️ problem, handled · quiet with no ⏳? /cpipe continue"""

RESUMED = """cpipe · resuming at the first unfinished step; finished steps are not redone.{note}"""

FEEDBACK = """cpipe · recording your findings. Each is reproduced, counted against the step that should have caught it,
turned into a learning and fixed as a follow-up."""

STEPS = ("general", "plan", "design", "build", "qa", "review")


def register(ctx):
    ctx.register_skill("run", SKILL)
    if hasattr(ctx, "register_command") and hasattr(ctx, "inject_message"):
        ctx.register_command("cpipe", lambda raw="": _start(ctx, raw),
                             description="Deliver a coding task unattended: plan, tests, build, review, report",
                             args_hint="[--kanban] [--budget <tier>] <task> | budget [<tier>] | continue | feedback <text> | learnings", argument_mode="text")
    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_llm_call", _steering)


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
        if msg.get("role") == "user" and text.startswith(START[:30]):
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
    if not message or message.startswith((START[:30], "/cpipe")):
        return None
    return {"context": STEERING} if _running(conversation_history) else None


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


def _roles(kanban):
    try:
        roles = dict(re.findall(r"(?m)^[ \t]+(\w+):[ \t]*([\w.-]+)", _config().read_text()))
    except OSError:
        roles = {}
    if not roles:
        return "this chat runs every step (no roles in cpipe.yaml)"
    if "plan" in roles or "build" in roles:  # one reader explores (skills/run/roles.md)
        roles.pop("explore", None)
    return (" · ".join(f"{role} {profile}" for role, profile in roles.items()) + ", the rest this chat"
            + (", as kanban cards" if kanban else ""))


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
        return "each profile's own setting (/cpipe budget <tier> sets one)"
    return f"{tier}, every profile reasons at {BUDGETS[tier]}"


def _budget(arg):
    tiers = ", ".join(BUDGETS)
    if not arg:
        return f"Budget: {_budget_line(_saved_budget(), False)}\nSet it: /cpipe budget <{tiers}|off>"
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
    return f"Budget: {_budget_line(arg if arg != 'off' else None, False)}. Saved in cpipe.yaml for every run."


def _learned_lines(name):
    path = _root() / "cpipe" / "learnings" / f"{name}.md"
    return [l for l in path.read_text().splitlines() if l.startswith("- ")] if path.exists() else []


def _learned():
    n = sum(len(_learned_lines(name)) for name in STEPS)
    if not n:
        return "nothing yet; every miss becomes a rule for the next run"
    return f"{n} rule{'s' * (n != 1)} from past runs, added to this one (/cpipe learnings)"


def _learnings():
    """The learnings and, per step, the misses that reached the user in the last 20 runs (skills/run/SKILL.md)."""
    import json
    base = _root() / "cpipe"
    out = []
    for name in (*STEPS, "retired"):
        lines = _learned_lines(name)
        if lines:
            out += [f"{name} ({len(lines)})", *lines, ""]
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
    return "\n".join(out).strip() or ("cpipe has no learnings yet. They are written when review, QA or you "
                                       "catch something a run should have seen (/cpipe feedback <what you found>).")


def _flags(task):
    """Leading --kanban and --budget <tier>, in any order; returns (kanban, budget, rest, error)."""
    kanban, budget, words = False, None, task.split()
    while words and words[0] in ("--kanban", "--budget"):
        if words.pop(0) == "--kanban":
            kanban = True
        elif words and words[0] in BUDGETS:
            budget = words.pop(0)
        else:
            return kanban, budget, "", "--budget needs one of: " + ", ".join(BUDGETS)
    return kanban, budget, " ".join(words), None


def _short(text, n=100):
    return text if len(text) <= n else text[:n - 1] + "…"


def _start(ctx, raw):
    task = raw.strip()
    first, _, rest = task.partition(" ")
    if first.lower() == "learnings" and not rest.strip():
        return _learnings()
    if first.lower() == "budget" and (not rest.strip() or rest.strip().lower() in (*BUDGETS, "off")):
        return _budget(rest.strip().lower())
    kanban, budget, brief, error = _flags(task)
    if error:
        return error + "\n\n" + USAGE
    if not brief:
        return USAGE
    from gateway.session_context import get_session_env
    key = get_session_env("HERMES_SESSION_KEY") or get_session_env("HERMES_SESSION_ID") or None
    try:
        if ctx.inject_message(START.format(skill=SKILL, root=_root()) + task, role="user",
                              session_key=key):
            first, _, rest = brief.partition(" ")
            if first.lower() == "feedback":
                return FEEDBACK
            if first.lower().rstrip(".!") in ("continue", "resume"):
                return RESUMED.format(note=f'\nYour note: "{_short(rest)}"' if rest else "")
            return STARTED.format(task=_short(brief), roles=_roles(kanban),
                                  budget=_budget_line(budget or _saved_budget(), kanban), learned=_learned())
        reason = "the chat did not accept the message"
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
    return (f"cpipe could not start the run here ({reason}).\n"
            "Type /cpipe:run followed by the task instead: it runs the same flow.")
