"""Explicit chat entry points: no model tools unless the operator opts in."""
import json

import pytest

pytest.importorskip("hermes_cli.kanban_db", reason="needs a Hermes Agent checkout")

from cpipe import settings, tools


class Context:
    def __init__(self):
        self.tools = {}
        self.commands = {}

    def register_tool(self, *, name, handler, **kwargs):
        self.tools[name] = handler

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = (handler, kwargs)


def test_command_only_is_the_default():
    assert settings.load({})["command_only"] is True
    assert settings.load({"command_only": False})["command_only"] is False


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_command_only_requires_a_boolean(value):
    with pytest.raises(ValueError, match="command_only"):
        settings.load({"command_only": value})


def test_default_registration_hides_delivery_tools_but_keeps_bridges_and_commands():
    ctx = Context()
    tools.register(ctx)
    assert set(ctx.tools) == {"cpipe_execute_intent", "cpipe_approve_plan", "cpipe_approve_action"}
    assert set(ctx.commands) == set(__import__("cpipe.intents", fromlist=["POLICIES"]).POLICIES)
    assert ctx.commands["cp-plan"][1]["args_hint"] == "[message]"


def test_tool_registration_is_explicitly_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "load", lambda: {"command_only": False})
    ctx = Context()
    tools.register(ctx)
    assert set(ctx.tools) == set(tools.SCHEMAS) | {"cpipe_execute_intent", "cpipe_approve_plan", "cpipe_approve_action"}
    assert set(ctx.commands) == set(__import__("cpipe.intents", fromlist=["POLICIES"]).POLICIES)
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", lambda args: {"accepted": args})
    assert json.loads(ctx.tools["cpipe_plan"]({"project": "app"}))["accepted"] == {"project": "app"}


@pytest.mark.parametrize("name", list(tools.HANDLERS))
def test_cached_model_calls_are_blocked_before_any_side_effect(name, monkeypatch):
    monkeypatch.setitem(tools.HANDLERS, name, lambda args: pytest.fail("model tool reached a handler"))
    result = json.loads(tools.call(name, {}))
    assert "optional cpipe workflow" in result["error"]
    assert result["cpipe_started"] is False
    assert "directly with normal tools" in result["next"]
    assert "/cp-" in result["error"]


def test_switching_to_command_only_blocks_previously_registered_tools(monkeypatch):
    cfg = {"command_only": False}
    monkeypatch.setattr(settings, "load", lambda: cfg)
    ctx = Context()
    tools.register(ctx)
    cfg["command_only"] = True
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", lambda args: pytest.fail("stale tool bypassed mode"))
    assert "optional cpipe workflow" in json.loads(ctx.tools["cpipe_plan"]({}))["error"]


@pytest.mark.parametrize("raw,size,project,expected_request", [
    ("app: Fix this button", "large", "app", "Fix this button"),
    ("Fix this button", "large", None, "Fix this button"),
    ("app", "large", None, "app"),
    ("app Fix this button", "large", None, "app Fix this button"),
    ("--size content app: Change this wording", "content", "app", "Change this wording"),
    ("--size small app: Fix this button", "small", "app", "Fix this button"),
    ("--size large app: Add password reset", "large", "app", "Add password reset"),
    ("--size=small app: Fix this button", "small", "app", "Fix this button"),
    ("--size=content Change this wording", "content", None, "Change this wording"),
    ("  --size   small   My-App:  Keep URL https://example.com/a:b  ", "small", "My-App", "Keep URL https://example.com/a:b"),
    ("app: Document --size small", "large", "app", "Document --size small"),
])
def test_plan_command_preserves_request_and_passes_size(raw, size, project, expected_request, monkeypatch):
    ctx = Context()
    tools.register(ctx)
    calls = []
    monkeypatch.setattr(tools, "plan", lambda args: calls.append(args) or {"task_id": "test", "status": "ready"})
    assert "ready" in tools.command("cp-plan", raw)
    expected = {"request": expected_request, "size": size}
    if project is not None:
        expected["project"] = project
    assert calls == [expected]


@pytest.mark.parametrize("raw", [
    "", "app:", ": request", "--size", "--size small", "--size=small",
    "--size tiny app: request", "--size= app: request", "--size small app:",
    "--size small : request", "--size small --size large app: request",
    "--engine hermes app: request",
])
def test_invalid_plan_arguments_never_start_a_delivery(raw, monkeypatch):
    monkeypatch.setattr(tools, "plan", lambda args: pytest.fail("invalid command started delivery"))
    ctx = Context()
    tools.register(ctx)
    reply = tools.command("cp-plan", raw)
    assert "usage:" in reply.lower()
    assert "--size" in reply


@pytest.mark.parametrize("name,raw,handler_name,expected", [
    ("cp-build", "test", "build", {"task_id": "test", "text": ""}),
    ("cp-ship", "test", "ship", {"task_id": "test", "text": ""}),
    ("cp-status", "test", "status", {"task_id": "test"}),
    ("cp-stop", "test", "stop", {"task_id": "test"}),
])
def test_explicit_steering_commands_still_work_in_command_only_mode(name, raw, handler_name, expected, monkeypatch):
    calls = []
    monkeypatch.setattr(tools, handler_name, lambda args: calls.append(args) or {"task_id": "test", "status": "ok"})
    ctx = Context()
    tools.register(ctx)
    assert "ok" in tools.command(name, raw)
    assert calls == [expected]
