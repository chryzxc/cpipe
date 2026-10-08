"""Frozen plan consent through Hermes' real approval transport; isolated board only."""
import json
from contextlib import closing

import pytest

from cpipe import native, report
from cpipe.runtime import hermes_api as h
from conftest import make_task


@pytest.fixture
def proposal(board, tmp_path, monkeypatch):
    ws = tmp_path / "work"
    (ws / ".cpipe").mkdir(parents=True)
    (ws / ".cpipe" / "state.json").write_text(json.dumps({
        "node": "proposal", "plan": "Authorize the event by ID before returning it.",
        "questions": [], "parts": [], "approved": False, "size": "large", "publish": False}))
    tid = make_task(board, assignee="cp:hermes", title="Calendar access fix",
                    workspace_kind="dir", workspace_path=str(ws), session_id="session-a")
    h.kb.block_task(board, tid, reason="Plan ready", kind="needs_input")
    monkeypatch.setattr(native, "_session", lambda explicit: explicit or "session-a")
    from tools import approval_context
    monkeypatch.setattr(approval_context, "_no_user_can_answer", lambda: False)
    return tid


def save_state(conn, tid, state):
    from pathlib import Path
    task = h.get_task(conn, tid)
    (Path(task.workspace_path) / ".cpipe" / "state.json").write_text(json.dumps(state))


def read_state(tid):
    with closing(h.connect(None)) as conn:
        return report.state_of(conn, h.get_task(conn, tid))


def request_args():
    context = native.before_turn(session_id="session-a")
    return json.loads(context["context"].split("Native approval arguments:\n", 1)[1])


def call(args, session="session-a"):
    from cpipe import plan_approval
    return plan_approval.request(args, session)


@pytest.mark.parametrize("choice", ["once", "deny"])
def test_gateway_ui_accept_reject_and_no_replay(proposal, monkeypatch, choice):
    from tools import approval
    from tools.approval_context import set_current_session_key, reset_current_session_key
    seen = []
    def notify(payload):
        seen.append(payload)
        assert approval.resolve_gateway_approval("route-a", choice, request_id=payload["request_id"])
    approval.register_gateway_notify("route-a", notify)
    token = set_current_session_key("route-a")
    try:
        args = request_args()
        import model_tools
        from tools.registry import registry
        from hermes_cli import plugins
        registered, hooks = {}, {}
        class Context:
            def register_tool(self, **kw):
                registered[kw["name"]] = kw["handler"]
            def register_hook(self, name, handler):
                hooks[name] = handler
        native.register(Context())
        monkeypatch.setattr(plugins, "has_hook", lambda name: name in hooks)
        monkeypatch.setattr(plugins, "invoke_hook", lambda name, **kw: [hooks[name](**kw)] if name in hooks else [])
        monkeypatch.setattr(registry, "dispatch", lambda name, arguments, **kw: registered[name](arguments, **kw))
        result = json.loads(model_tools.handle_function_call(
            "cpipe_approve_plan", args, session_id="session-a", skip_pre_tool_call_hook=True))
        assert result["status"] == ("approved" if choice == "once" else "rejected")
        assert seen[0]["allow_session"] is False and seen[0]["allow_permanent"] is False
        assert "Authorize the event by ID" in seen[0]["description"]
        assert "draft PR if enabled" in seen[0]["description"]
        assert not approval._gateway_queues.get("route-a")
        call(args)
        assert len(seen) == 1
        with closing(h.connect(None)) as conn:
            view = h.kb.get_task(conn, proposal)
            state = report.state_of(conn, view)
            directives = [c for c in h.comments_after(conn, proposal) if c.author == "cpipe:approved"]
            if choice == "once":
                from cpipe.round import feedback
                consumed = {**state, "last_comment_id": 0}
                assert feedback(conn, proposal, consumed) == ""
                assert consumed["approved"] is True
                assert consumed["publish"] is False and not consumed.get("ready")
                assert feedback(conn, proposal, consumed) == ""
        assert bool(directives) is (choice == "once")
        assert state["approved"] is False  # worker consumes the existing directive on its next round
        assert state["publish"] is False
        assert view.status == ("ready" if choice == "once" else "blocked")
        if choice == "deny":
            assert "Native approval arguments" not in native.before_turn(session_id="session-a")["context"]
    finally:
        approval.unregister_gateway_notify("route-a")
        reset_current_session_key(token)


def test_before_turn_uses_approval_not_clarify_for_ready_plan(proposal):
    guidance = native.before_turn(session_id="session-a")["context"]
    assert "cpipe_approve_plan" in guidance
    assert "Call the native clarify tool now" not in guidance


def test_questions_still_use_native_clarification(proposal, board):
    state = report.state_of(board, h.get_task(board, proposal))
    state["questions"] = ["Keep existing sharing behavior?"]
    save_state(board, proposal, state)
    assert "Native approval arguments" not in native.before_turn(session_id="session-a")["context"]
    assert "Native clarify arguments" in native.before_turn(session_id="session-a")["context"]


@pytest.mark.parametrize("failure", ["timeout", "cancelled", "notify_failed", "session", "always"])
def test_non_one_operation_answer_fails_closed(proposal, monkeypatch, failure):
    from cpipe import plan_approval
    args = request_args()
    response = {"resolved": True, "choice": failure}
    if failure == "timeout":
        response = {"resolved": False, "choice": None}
    elif failure in ("cancelled", "notify_failed"):
        response = {"resolved": True, "choice": "once", failure: True}
    monkeypatch.setattr(plan_approval, "_prompt", lambda *a: response)
    result = call(args)
    assert result["status"] == "waiting_for_approval"
    assert read_state(proposal)["approved"] is False


def test_wrong_session_revision_and_unknown_fields_never_prompt(proposal, monkeypatch):
    from cpipe import plan_approval
    seen = []
    monkeypatch.setattr(plan_approval, "_prompt", lambda *a: seen.append(a))
    args = request_args()
    for invalid, session in [
        (args, "session-b"),
        ({**args, "plan_revision": "stale"}, "session-a"),
        ({**args, "approved": True}, "session-a"),
    ]:
        assert "error" in call(invalid, session)
    assert seen == []


def test_changed_plan_while_ui_open_is_not_approved(proposal, monkeypatch, board):
    from cpipe import plan_approval
    args = request_args()
    def prompt(*a):
        state = report.state_of(board, h.get_task(board, proposal))
        state["plan"] = "Different scope"
        save_state(board, proposal, state)
        return {"resolved": True, "choice": "once"}
    monkeypatch.setattr(plan_approval, "_prompt", prompt)
    assert call(args)["status"] == "stale_plan"
    assert report.state_of(board, h.get_task(board, proposal))["approved"] is False


def test_cli_native_callback_has_no_session_or_permanent_buttons(proposal, monkeypatch):
    import tools.terminal_tool as terminal
    from tools import approval
    seen = []
    def approve(command, description, **kw):
        seen.append(kw)
        return "once"
    monkeypatch.setattr(terminal, "_get_approval_callback", lambda: approve)
    monkeypatch.setattr(approval, "_yolo_active", lambda: True)
    assert call(request_args())["status"] == "approved"
    assert seen[0]["allow_session"] is False and seen[0]["allow_permanent"] is False
    assert seen[0]["title"] == "Approve this development plan?"


def test_unattended_or_missing_human_channel_never_auto_approves(proposal, monkeypatch):
    from tools import approval_context
    monkeypatch.setattr(approval_context, "_no_user_can_answer", lambda: True)
    assert call(request_args())["status"] == "waiting_for_approval"


def test_feedback_while_dialog_open_invalidates_approval(proposal, monkeypatch):
    from cpipe import plan_approval
    args = request_args()
    def prompt(*a):
        with closing(h.connect(None)) as conn:
            h.comment(conn, proposal, "Change the authorization scope", author="user")
        return {"resolved": True, "choice": "once"}
    monkeypatch.setattr(plan_approval, "_prompt", prompt)
    assert call(args)["status"] == "stale_plan"
    with closing(h.connect(None)) as conn:
        task = h.get_task(conn, proposal)
        assert task is not None and task.status == "blocked"
        assert not any(c.author == "cpipe:approved" for c in h.comments_after(conn, proposal))


def test_duplicate_request_during_dialog_does_not_open_second_ui(proposal, monkeypatch):
    from cpipe import plan_approval
    args = request_args()
    seen = []
    def prompt(*a):
        seen.append(a)
        assert call(args)["status"] == "approval_pending"
        return {"resolved": True, "choice": "deny"}
    monkeypatch.setattr(plan_approval, "_prompt", prompt)
    assert call(args)["status"] == "rejected"
    assert len(seen) == 1


def test_missing_interactive_transport_does_not_approve(proposal, monkeypatch):
    import tools.terminal_tool as terminal
    monkeypatch.setattr(terminal, "_get_approval_callback", lambda: None)
    assert call(request_args())["status"] == "waiting_for_approval"


def test_registered_tool_requires_human_not_approved_argument(proposal):
    from cpipe import plan_approval
    registered = {}
    class Context:
        def register_tool(self, **kw):
            registered[kw["name"]] = kw
    plan_approval.register(Context())
    tool = registered["cpipe_approve_plan"]
    assert tool["schema"]["parameters"]["additionalProperties"] is False
    assert tool["schema"]["parameters"]["required"] == ["task_id", "plan_revision"]
    assert "approved" not in tool["schema"]["parameters"]["properties"]
