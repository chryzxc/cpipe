"""cpipe: a Hermes plugin made of skills. The model runs the delivery; this file only registers the run skill and /cpipe."""

from pathlib import Path

START = ("Load the skill cpipe:run with skill_view and follow it to deliver this task. First start its wake-up "
         "(Turn limits). Run it unattended to the report: this is my last message, so never stop to ask or wait.\n\n")

USAGE = """Usage: /cpipe [--kanban] <task>
Delivers a coding task unattended in this chat and reports back here.
Steps: plan, tests first, build, verify with the project's checks, QA and independent review, commit.
  --kanban   hand the role steps to your profiles as cards on your Hermes kanban board
Example: /cpipe fix the date filter on the orders page and open a draft pull request"""

STARTED = """cpipe started: {task}
Flow: set up → explore → design → decide → plan → build (tests first) → verify → QA + review → clean → commit → report
Roles: {roles}
No questions: open choices are decided and listed in the report.
Push or pull request: only if the task asks for one.
It posts a line as each step starts. If a turn ends early it resumes itself; after a restart, say "continue"."""


def register(ctx):
    ctx.register_skill("run", Path(__file__).parent / "skills" / "run" / "SKILL.md")
    if hasattr(ctx, "register_command") and hasattr(ctx, "inject_message"):
        ctx.register_command("cpipe", lambda raw="": _start(ctx, raw),
                             description="Deliver a coding task unattended: plan, tests, build, review, report",
                             args_hint="[--kanban] <task>", argument_mode="text")


def _start(ctx, raw):
    task = raw.strip()
    kanban = task.startswith("--kanban")
    if not task.removeprefix("--kanban").strip():
        return USAGE
    from gateway.session_context import get_session_env
    key = get_session_env("HERMES_SESSION_KEY") or get_session_env("HERMES_SESSION_ID") or None
    try:
        if ctx.inject_message(START + task, role="user", session_key=key):
            brief = " ".join(task.removeprefix("--kanban").split())
            return STARTED.format(
                task=brief if len(brief) <= 100 else brief[:99] + "…",
                roles="cards on your kanban board for the profiles in cpipe.yaml" if kanban
                else "your profiles from cpipe.yaml where set, this chat for the rest")
        reason = "the chat did not accept the message"
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
    return (f"cpipe could not start the run here ({reason}).\n"
            "Type /cpipe:run followed by the task instead: it runs the same flow.")
