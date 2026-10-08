"""A build command can start new work only after its exact native confirmation."""
import json
import re

import pytest

from cpipe import intents, native, tools, engines
from cpipe.runtime import hermes_api as h


class Context:
    def __init__(self):
        self.prompt = ""

    def inject_message(self, message, **kwargs):
        self.prompt = message
        return True


@pytest.fixture
def start(board, monkeypatch):
    monkeypatch.setattr(intents, "_session_key", lambda: "chat-a")
    monkeypatch.setattr(native, "_session", lambda explicit: explicit or "chat-a")
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    ctx = Context()
    intents.dispatch(ctx, "cp-build", "Save submitted applications and their submission dates")
    match = re.search(r"receipt: ([\w-]+)", ctx.prompt)
    assert match is not None
    token = match[1]
    payload = {"request": "Save submitted applications and their submission dates", "project": "jobfinder"}
    args = {"token": token, "operation": "build", "arguments": payload}
    calls = []
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", lambda p: calls.append(p) or {"task_id": "new-delivery", "size": p["size"]})
    return args, calls


def reply(form, choice="Start a small delivery", **extra):
    return {"responses": [{"question": form["questions"][0]["question"], "user_response": choice}], **extra}


def finish(form):
    result = native.after_tool(session_id="chat-a", tool_name="clarify", args=form, result=reply(form))
    assert result["status"] == "needs_routing"
    for value in ("Single Hermes profile (default or another)", "hermes", "Start with this route"):
        form = result["clarify"]
        result = native.after_tool(session_id="chat-a", tool_name="clarify", args=form, result=reply(form, value))
    return result


def test_build_confirmation_starts_same_request_once(start):
    args, calls = start
    out = json.loads(intents.execute(args))
    assert out["status"] == "needs_confirmation" and not calls
    form = out["clarify"]
    result = finish(form)
    assert result["task_id"] == "new-delivery"
    assert len(calls) == 1
    assert calls[0]["size"] == "small"
    assert calls[0]["routing"]["mode"] == "single"
    assert calls[0]["request"] == args["arguments"]["request"]
    assert native.after_tool(session_id="chat-a", tool_name="clarify", args=form, result=reply(form)) is None
    assert json.loads(intents.execute(args))["task_id"] == "new-delivery"
    assert len(calls) == 1


def test_build_receipt_cannot_call_plan_or_self_confirm(start):
    args, calls = start
    assert "incompatible" in json.loads(intents.execute({**args, "operation": "plan"}))["error"]
    out = json.loads(intents.execute({**args, "arguments": {**args["arguments"], "confirmed": True}}))
    assert out["status"] == "needs_confirmation" and not calls


@pytest.mark.parametrize("change", ["wrong-session", "wrong-key", "expired", "timeout", "partial", "unrelated", "free-text", "changed-form", "new-delivery"])
def test_invalid_confirmation_does_not_start(start, board, monkeypatch, change):
    args, calls = start
    form = json.loads(intents.execute(args))["clarify"]
    session = "chat-a"
    result = reply(form)
    if change == "wrong-session":
        session = "chat-b"
    elif change == "wrong-key":
        monkeypatch.setattr(intents, "_session_key", lambda: "chat-b")
    elif change == "expired":
        with intents._connect() as conn:
            conn.execute("UPDATE intents SET expires=0")
    elif change == "timeout":
        result["timed_out"] = True
    elif change == "partial":
        result["responses"] = []
    elif change == "unrelated":
        form = {"questions": [{"question": "Tea?"}]}
        result = reply(form)
    elif change == "free-text":
        result = reply(form, "yes, change another repo instead")
    elif change == "changed-form":
        form["questions"][0]["choices"] = ["Start a small delivery"]
    elif change == "new-delivery":
        h.kb.create_task(board, title="another delivery", assignee="cp:hermes", session_id="chat-a")
    native.after_tool(session_id=session, tool_name="clarify", args=form, result=result)
    assert not calls


def test_cancel_consumes_receipt_without_work(start):
    args, calls = start
    form = json.loads(intents.execute(args))["clarify"]
    result = native.after_tool(session_id="chat-a", tool_name="clarify", args=form, result=reply(form, "Cancel"))
    assert result["status"] == "cancelled" and not calls
    assert json.loads(intents.execute(args))["status"] == "cancelled"


def test_pending_form_preserves_scope_on_retry(start):
    args, calls = start
    first = json.loads(intents.execute(args))
    retry = json.loads(intents.execute({**args, "arguments": {"request": "different work", "project": "other"}}))
    assert retry == first
    form = first["clarify"]
    finish(form)
    assert calls[0]["request"] == args["arguments"]["request"]


def test_existing_delivery_is_not_replaced_by_new_start(start, board):
    args, calls = start
    h.kb.create_task(board, title="existing delivery", assignee="cp:hermes", session_id="chat-a")
    out = json.loads(intents.execute(args))
    assert "same-session" in out["error"] and not calls


def test_confirmation_outcome_reaches_next_llm_once(start):
    args, _ = start
    form = json.loads(intents.execute(args))["clarify"]
    finish(form)
    assert native.before_turn(session_id="chat-b") is None
    assert "new-delivery" in native.before_turn(session_id="chat-a")["context"]
    assert native.before_turn(session_id="chat-a") is None


def test_handler_failure_is_reported_without_replaying(start, monkeypatch):
    args, _ = start
    form = json.loads(intents.execute(args))["clarify"]
    calls = []
    def fail(payload):
        calls.append(payload)
        raise RuntimeError("submission interrupted")
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", fail)
    result = finish(form)
    assert "submission interrupted" in result["error"]
    assert "submission interrupted" in native.before_turn(session_id="chat-a")["context"]
    assert native.after_tool(session_id="chat-a", tool_name="clarify", args=form, result=reply(form)) is None
    assert "claimed" in json.loads(intents.execute(args))["error"]
    assert len(calls) == 1


def test_confirmation_creates_real_same_session_small_delivery(start, board, tmp_path, monkeypatch):
    from cpipe import settings
    import subprocess
    args, _ = start
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "--allow-empty", "-qm", "chore: baseline"], check=True)
    cfg = settings.load({})
    monkeypatch.setattr(tools.settings, "load", lambda: cfg)
    monkeypatch.setattr(tools, "resolve_project", lambda *a: ("jobfinder", repo))
    from tools import kanban_tools, async_delegation
    monkeypatch.setattr(async_delegation, "_current_origin_session_id", lambda: "chat-a")
    monkeypatch.setattr(kanban_tools, "_persisted_session_id", lambda sid: sid)
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", tools.plan)
    form = json.loads(intents.execute(args))["clarify"]
    assert h.list_cards(board) == []
    result = finish(form)
    task = h.get_task(board, result["task_id"])
    assert task is not None
    assert task.session_id == "chat-a" and task.assignee == "default" and task.status == "ready"
    assert any(c.author == "cpipe:size" and c.body == "small" for c in h.comments_after(board, task.id))
    assert args["arguments"]["request"] in task.body
    assert native.after_tool(session_id="chat-a", tool_name="clarify", args=form, result=reply(form)) is None
    assert len(h.list_cards(board)) == 1
