"""Natural-language commands enqueue the current session; no live side effects."""
import json
import re

import pytest

from cpipe import intents, tools

REAL_SESSION_KEY = intents._session_key


class Context:
    def __init__(self, accepted=True):
        self.commands, self.tools, self.messages = {}, {}, []
        self.accepted = accepted

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = (handler, kwargs)

    def register_tool(self, name, **kwargs):
        self.tools[name] = kwargs

    def inject_message(self, message, **kwargs):
        self.messages.append((message, kwargs))
        return self.accepted


def token(ctx):
    return re.search(r"receipt: ([\w-]+)", ctx.messages[-1][0])[1]


@pytest.fixture(autouse=True)
def session(monkeypatch):
    monkeypatch.setattr(intents, "_session_key", lambda: "chat-a")
    from cpipe import engines, native
    monkeypatch.setattr(native, "_session", lambda explicit: explicit or "chat-a")
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))


@pytest.mark.parametrize("command", intents.POLICIES)
def test_commands_preserve_natural_language_in_same_session(hermes_home, monkeypatch, command):
    ctx = Context()
    intents.register(ctx)
    monkeypatch.setattr(tools, "command", lambda *a: pytest.fail("legacy id parser called"))
    message = "let’s use the earlier changes: 'footer' -- keep everything else"
    handler, metadata = ctx.commands[command]
    assert "queued" in handler(message)
    prompt, kwargs = ctx.messages[0]
    assert prompt.endswith(message)
    assert "existing session history" in prompt
    assert kwargs == {"role": "user", "session_key": "chat-a"}
    assert metadata["args_hint"] == "[message]"
    assert metadata["argument_mode"] == "text"


def test_ship_adopts_existing_work_and_uses_specialists_before_draft(hermes_home, monkeypatch):
    ctx = Context()
    calls = []
    monkeypatch.setattr(tools, "stage", lambda entry, args: calls.append((entry, args)) or {"stage": entry})
    assert "queued" in intents.dispatch(ctx, "cp-ship", "let’s create a draft PR for this")
    prompt = ctx.messages[0][0]
    assert "without a cpipe delivery" in prompt
    assert "Default to draft" in prompt and "only when the user explicitly asks" in prompt
    assert "never a direct-tools bypass" in prompt and "Never merge" in prompt
    args = {"request": "Resolve existing scope"}
    result = finish_routing({"token": token(ctx), "operation": "ship", "arguments": args})
    assert result == {"stage": "ship"} and len(calls) == 1
    assert calls[0][0] == "ship" and calls[0][1]["request"] == args["request"]
    assert calls[0][1]["routing"]["mode"] == "single"


@pytest.mark.parametrize("name", intents.POLICIES)
def test_empty_message_uses_existing_context(hermes_home, name):
    ctx = Context()
    assert "queued" in intents.dispatch(ctx, name)
    assert "session history" in ctx.messages[0][0]


def test_unavailable_queue_is_honest_and_revokes_receipt(hermes_home):
    ctx = Context(accepted=False)
    assert "No work started" in intents.dispatch(ctx, "cp-plan", "do this")
    result = json.loads(intents.execute({"token": token(ctx), "operation": "plan", "arguments": {}}))
    assert "error" in result


@pytest.mark.parametrize("operation", ["plan", "review", "fix", "verify"])
@pytest.mark.parametrize("existing_receipts", [False, True])
def test_ordinary_chat_continues_directly_without_starting_cpipe(hermes_home, monkeypatch, operation, existing_receipts):
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", lambda *a: pytest.fail("backend ran"))
    monkeypatch.setattr(tools, "stage", lambda *a: pytest.fail("stage backend ran"))
    if existing_receipts:
        intents._connect().close()
    result = json.loads(intents.execute({"token": "invented", "operation": operation, "arguments": {}}))
    assert "optional cpipe workflow" in result["error"]
    assert result["cpipe_started"] is False
    assert "directly with normal tools" in result["next"]
    assert "Do not require the user to send a /cp command" in result["next"]
    assert (hermes_home / "cpipe-intents.db").exists() == existing_receipts


def finish_routing(args):
    from cpipe import native
    pending = json.loads(intents.execute(args))
    assert pending["status"] == "needs_routing"
    for value in ("Single Hermes profile (default or another)", "hermes", "Start with this route"):
        form = pending["clarify"]
        pending = native.after_tool(session_id="chat-a", tool_name="clarify", args=form,
            result={"responses": [{"question": form["questions"][0]["question"], "user_response": value}]})
    return {k: v for k, v in pending.items() if k != "context"}


def test_receipts_are_command_scoped_session_bound_and_idempotent(hermes_home, monkeypatch):
    ctx = Context()
    intents.dispatch(ctx, "cp-plan", "implement this")
    calls = []
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", lambda args: calls.append(args) or {"task_id": "test-only"})
    args = {"token": token(ctx), "operation": "build", "arguments": {"request": "resolved context"}}
    assert "incompatible" in json.loads(intents.execute(args))["error"]
    args["operation"] = "plan"
    monkeypatch.setattr(intents, "_session_key", lambda: "other-chat")
    assert "wrong-session" in json.loads(intents.execute(args))["error"]
    monkeypatch.setattr(intents, "_session_key", lambda: "chat-a")
    assert finish_routing(args) == {"task_id": "test-only"}
    assert json.loads(intents.execute(args)) == {"task_id": "test-only"}
    assert len(calls) == 1


def test_expired_receipt_cannot_start_work(hermes_home, monkeypatch):
    ctx = Context()
    intents.dispatch(ctx, "cp-plan", "implement this")
    with intents._connect() as conn:
        conn.execute("UPDATE intents SET expires=0")
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", lambda *a: pytest.fail("expired request ran"))
    assert "expired" in json.loads(intents.execute({"token": token(ctx), "operation": "plan", "arguments": {}}))["error"]


def test_claimed_failure_is_not_replayed(hermes_home, monkeypatch):
    ctx = Context()
    intents.dispatch(ctx, "cp-plan", "implement this")
    calls = []
    def fail(args):
        calls.append(args)
        raise RuntimeError("test interruption after possible commit")
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", fail)
    args = {"token": token(ctx), "operation": "plan", "arguments": {"request": "resolved work"}}
    assert "inspect status" in finish_routing(args)["error"]
    assert "already claimed" in json.loads(intents.execute(args))["error"]
    assert len(calls) == 1


@pytest.mark.parametrize("surface", ["cli", "desktop", "denied-desktop"])
def test_real_hermes_injection_routes_command_into_owning_session(hermes_home, monkeypatch, surface):
    from queue import Queue
    from types import SimpleNamespace
    from hermes_cli import plugins
    from hermes_cli.plugins_manifest import PluginManifest

    monkeypatch.setattr(intents, "_session_key", REAL_SESSION_KEY)
    monkeypatch.setenv("HERMES_SESSION_KEY", "test-durable-chat")
    manager = plugins.PluginManager(scope_key=str(hermes_home))
    ctx = plugins.PluginContext(PluginManifest(name="cpipe"), manager)
    captured = []
    queue = Queue()
    if surface == "cli":
        setattr(manager, "_cli_ref", SimpleNamespace(_agent_running=False, _pending_input=queue))
    else:
        manager.set_tui_message_injector(object(), lambda **kw: captured.append(kw) or True)
        monkeypatch.setattr(plugins, "load_config_readonly", lambda: {
            "plugins": {"entries": {"cpipe": {"allow_gateway_injection": surface == "desktop"}}}})
    result = intents.dispatch(ctx, "cp-ship", "let’s create a draft PR for this")
    if surface == "denied-desktop":
        assert "No work started" in result and not captured
    elif surface == "cli":
        assert "queued" in result
        assert queue.get_nowait().endswith("let’s create a draft PR for this")
    else:
        assert "queued" in result
        assert captured[0]["session_key"] == "test-durable-chat"
        assert captured[0]["plugin_id"] == "cpipe"
        assert captured[0]["content"].endswith("let’s create a draft PR for this")
