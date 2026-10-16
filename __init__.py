"""cpipe: a Hermes plugin made of skills. The model runs the delivery; this file only registers them."""

from pathlib import Path

SKILLS = ("run", "plan", "design", "build", "verify", "qa", "review", "report")
START = ("Load the skill cpipe:run with skill_view and follow it to deliver this task. Run it unattended to the "
         "report: this is my last message, so never stop to ask or wait.\n\n")


def register(ctx):
    skills = Path(__file__).parent / "skills"
    for name in SKILLS:
        ctx.register_skill(name, skills / name / "SKILL.md")
    if hasattr(ctx, "register_command") and hasattr(ctx, "inject_message"):
        ctx.register_command("cpipe", lambda raw="": _start(ctx, raw),
                             description="Deliver a task: explore, design, plan, build, verify, QA, review, report",
                             args_hint="<task>", argument_mode="text")


def _start(ctx, raw):
    if not raw.strip():
        return "Usage: /cpipe <task>"
    from gateway.session_context import get_session_env
    key = get_session_env("HERMES_SESSION_KEY") or get_session_env("HERMES_SESSION_ID") or None
    try:
        if ctx.inject_message(START + raw.strip(), role="user", session_key=key):
            return "cpipe: started."
    except Exception:
        pass
    return "cpipe: could not start here. Type /cpipe:run followed by the task instead."
