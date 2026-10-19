"""cpipe: a Hermes plugin made of skills. The model runs the delivery; this file only registers the run skill and /cpipe."""

import os
import re
from pathlib import Path

START = ("Load the skill cpipe:run with skill_view and follow it to deliver this task. Run it unattended to the "
         "report: this is my last message, so never stop to ask or wait.\n\n")

USAGE = """Usage: /cpipe [--kanban] <task>
Delivers a coding task unattended in this chat and reports back here.
Steps: plan, tests first, build, verify with the project's checks, QA and independent review, commit.
  --kanban          hand the role steps to your profiles as cards on your Hermes kanban board
  /cpipe continue   pick up a run that stopped, at its first unfinished step
Example: /cpipe fix the date filter on the orders page and open a draft pull request"""

STARTED = """▶ cpipe: {task}
Unattended from here to a report in this chat: plan → tests first → build → verify → QA + review → commit.
{roles}
Open questions are decided for you and listed in the report. No push or pull request unless you asked.
One ▶ line per step. If it goes quiet, send /cpipe continue."""

RESUMED = """↻ cpipe: continuing the run in progress
It reads the run log and picks up at the first unfinished step; finished steps are not redone.{note}"""


def register(ctx):
    ctx.register_skill("run", Path(__file__).parent / "skills" / "run" / "SKILL.md")
    if hasattr(ctx, "register_command") and hasattr(ctx, "inject_message"):
        ctx.register_command("cpipe", lambda raw="": _start(ctx, raw),
                             description="Deliver a coding task unattended: plan, tests, build, review, report",
                             args_hint="[--kanban] <task> | continue", argument_mode="text")


def _roles(kanban):
    path = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "cpipe.yaml"
    try:
        found = re.findall(r"(?m)^[ \t]+(\w+):[ \t]*([\w.-]+)", path.read_text())
    except OSError:
        found = []
    if not found:
        return ("Roles: none set in cpipe.yaml, so nothing goes to the board; every step runs in this chat." if kanban
                else "Roles: every step runs in this chat (no roles set in cpipe.yaml).")
    return (("Roles, as cards on your kanban board: " if kanban else "Roles: ")
            + " · ".join(f"{role} {profile}" for role, profile in found) + "; the rest in this chat.")


def _short(text, n=100):
    return text if len(text) <= n else text[:n - 1] + "…"


def _start(ctx, raw):
    task = raw.strip()
    kanban = task.startswith("--kanban")
    brief = " ".join(task.removeprefix("--kanban").split())
    if not brief:
        return USAGE
    from gateway.session_context import get_session_env
    key = get_session_env("HERMES_SESSION_KEY") or get_session_env("HERMES_SESSION_ID") or None
    try:
        if ctx.inject_message(START + task, role="user", session_key=key):
            first, _, rest = brief.partition(" ")
            if first.lower().rstrip(".!") in ("continue", "resume"):
                return RESUMED.format(note=f'\nYour note goes with it: "{_short(rest)}"' if rest else "")
            return STARTED.format(task=_short(brief), roles=_roles(kanban))
        reason = "the chat did not accept the message"
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
    return (f"cpipe could not start the run here ({reason}).\n"
            "Type /cpipe:run followed by the task instead: it runs the same flow.")
