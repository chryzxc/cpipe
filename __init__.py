"""cpipe: a Hermes plugin made of skills. The model runs the delivery; this file only registers the run skill and /cpipe."""

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
Example: /cpipe --budget small fix the date filter on the orders page and open a draft pull request"""

STARTED = """▶️ cpipe: {task}
Unattended from here to a report in this chat: plan → tests first → build → verify → QA + review → commit.
{roles}
{budget}
Open questions are decided for you and listed in the report. No push or pull request unless you asked.
One ▶️ line per step. ⏳ means it waits on a check and resumes on its own.
If it stops with no ⏳ or Hermes restarts, send /cpipe continue."""

RESUMED = """🔄 cpipe: continuing the run in progress
It reads the run log and picks up at the first unfinished step; finished steps are not redone.{note}"""


def register(ctx):
    ctx.register_skill("run", SKILL)
    if hasattr(ctx, "register_command") and hasattr(ctx, "inject_message"):
        ctx.register_command("cpipe", lambda raw="": _start(ctx, raw),
                             description="Deliver a coding task unattended: plan, tests, build, review, report",
                             args_hint="[--kanban] [--budget <tier>] <task> | budget [<tier>] | continue", argument_mode="text")


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
        return ("Roles: none set in cpipe.yaml, so nothing goes to the board; every step runs in this chat." if kanban
                else "Roles: every step runs in this chat (no roles set in cpipe.yaml).")
    if "plan" in roles or "build" in roles:  # one reader explores (skills/run/roles.md)
        roles.pop("explore", None)
        if "plan" in roles:
            roles["plan"] += " (explores too)"
        else:
            roles["build"] += " (explores and plans too)"
    return (("Roles, as cards on your kanban board: " if kanban else "Roles: ")
            + " · ".join(f"{role} {profile}" for role, profile in roles.items()) + "; the rest in this chat.")


def _saved_budget():
    try:
        found = re.search(r"(?m)^budget:[ \t]*(\w+)", _config().read_text())
    except OSError:
        return None
    return found.group(1) if found and found.group(1) in BUDGETS else None


def _budget_line(tier, kanban):
    if kanban:
        return "Budget: not applied on the board (cards take no reasoning setting)."
    if not tier:
        return "Budget: not set, each profile reasons at its own setting (/cpipe budget <tier> saves one)."
    return f"Budget: {tier}, every profile run reasons at {BUDGETS[tier]}."


def _budget(arg):
    tiers = ", ".join(BUDGETS)
    if not arg:
        return _budget_line(_saved_budget(), False) + f"\nSet it: /cpipe budget <{tiers}|off>"
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
    return _budget_line(arg if arg != "off" else None, False) + " Saved in cpipe.yaml for every run."


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
            if first.lower().rstrip(".!") in ("continue", "resume"):
                return RESUMED.format(note=f'\nYour note goes with it: "{_short(rest)}"' if rest else "")
            return STARTED.format(task=_short(brief), roles=_roles(kanban),
                                  budget=_budget_line(budget or _saved_budget(), kanban))
        reason = "the chat did not accept the message"
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
    return (f"cpipe could not start the run here ({reason}).\n"
            "Type /cpipe:run followed by the task instead: it runs the same flow.")
