"""Command-to-runtime regressions on an isolated native Hermes board."""
import json
from argparse import Namespace

import pytest

from cpipe import native, settings, tools
from cpipe.runtime import cli, hermes_api as h
from conftest import make_task


def test_plain_assignee_is_owned_by_provenance_not_name(board):
    owned = make_task(board, assignee="archon", created_by="cpipe")
    unrelated = make_task(board, assignee="archon", created_by="user")
    assert owned in {t.id for t in h.list_cards(board)}
    assert unrelated not in {t.id for t in h.list_cards(board)}


def test_cli_status_uses_actual_delivery_report(board, capsys):
    tid = make_task(board, assignee="cp:hermes", created_by="cpipe")
    assert cli.handle(Namespace(wr_command="status", board=None, json=True)) == 0
    assert json.loads(capsys.readouterr().out)[0]["task_id"] == tid


def test_new_card_has_enforced_budget_and_verified_wake(board, monkeypatch):
    from tools import kanban_tools as kt
    from hermes_cli import kanban_db_notify as notify
    monkeypatch.setattr(kt, "_resolve_notify_target", lambda: {
        "platform": "tui", "chat_id": "chat-a", "notifier_profile": "default"})
    tid = h.create_card(board, title="Plan fix", body="bounded fix", assignee="archon",
                        repo="/not-mounted-in-this-test", branch="fix/test")
    task = h.get_task(board, tid)
    assert task.max_runtime_seconds and task.idempotency_key
    assert notify.list_notify_subs(board, tid)[0]["delivery_mode"] == "notify+wake"


def test_unavailable_wake_never_silently_queues_chat_delivery(board, monkeypatch):
    from tools import kanban_tools as kt
    monkeypatch.setattr(kt, "_resolve_notify_target", lambda: {"platform": "tui", "chat_id": "chat-a"})
    from hermes_cli import kanban_db_notify as notify
    monkeypatch.setattr(notify, "add_notify_sub", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("transport unavailable")))
    with pytest.raises(RuntimeError, match="subscription"):
        h.create_card(board, title="Plan fix", body="fix", assignee="archon", repo="/unused", branch="fix/test")
    assert all(t.status == "blocked" for t in h.list_cards(board))


def test_plain_profile_cannot_be_double_dispatched_by_native_runtime(board, monkeypatch):
    from cpipe.runtime import dispatch_guard
    from hermes_cli import kanban_db_dispatch as dispatch
    owned = make_task(board, assignee="default", created_by="cpipe")
    native_task = make_task(board, assignee="default", created_by="user")
    dispatch_guard.install()
    monkeypatch.setattr(dispatch, "_profile_exists_fn", lambda: lambda name: True)
    monkeypatch.setattr(dispatch, "check_respawn_guard", lambda *a, **kw: None)
    result = dispatch.dispatch_once(board, dry_run=True, reconcile_orphans=False)
    assert owned not in {row[0] for row in result.spawned}
    assert native_task in {row[0] for row in result.spawned}


def waiting(board, tmp_path, node, **state):
    ws = tmp_path / node
    (ws / ".cpipe").mkdir(parents=True)
    (ws / ".cpipe/state.json").write_text(json.dumps({"node": node, **state}))
    tid = make_task(board, assignee="cp:hermes", created_by="cpipe", session_id="chat-a",
                    workspace_kind="dir", workspace_path=str(ws))
    h.kb.block_task(board, tid, reason="Awaiting user choice", kind="needs_input")
    return tid


@pytest.mark.parametrize("node,expected", [("reviewed", "Fix findings"), ("verified", "Publish draft PR"),
                                         ("handoff", "Mark PR ready")])
def test_each_human_handoff_offers_native_ui_not_chat_proceed(board, tmp_path, node, expected):
    waiting(board, tmp_path, node, findings=["REV-001 fix behavior"], pr_url="https://github.com/a/b/pull/1")
    context = native.before_turn(session_id="chat-a")
    assert context and expected in context["context"]
    assert ("Native clarify arguments:" in context["context"] if node == "reviewed"
            else "cpipe_approve_action" in context["context"])


def test_question_batch_continues_inside_same_tool_loop(board, tmp_path):
    waiting(board, tmp_path, "proposal", questions=[f"Question {i}?" for i in range(7)])
    form = json.loads(native.before_turn(session_id="chat-a")["context"].split("Native clarify arguments:\n")[1])
    result = {"responses": [{"question": q["question"], "user_response": "No"} for q in form["questions"]]}
    native.after_tool(session_id="chat-a", tool_name="clarify", args=form, result=result)
    transformed = native.transform_result(session_id="chat-a", tool_name="clarify", args=form, result=result)
    assert transformed and "Question 5?" in transformed and "Question 6?" in transformed


def test_cancelled_native_form_does_not_consume_answers(board, tmp_path):
    tid = waiting(board, tmp_path, "ask")
    form = json.loads(native.before_turn(session_id="chat-a")["context"].split("Native clarify arguments:\n")[1])
    data = {"outcome": "cancelled", "responses": [{"question": form["questions"][0]["question"], "user_response": "proceed"}]}
    assert native.after_tool(session_id="chat-a", tool_name="clarify", args=form, result=data) is None
    assert h.get_task(board, tid).status == "blocked"


def test_native_continuation_launch_failure_is_visible_in_same_tool_loop(board, tmp_path, monkeypatch):
    tid = waiting(board, tmp_path, "ask")
    form = json.loads(native.before_turn(session_id="chat-a")["context"].split("Native clarify arguments:\n")[1])
    reply = {"outcome": "submitted", "responses": [{"question": form["questions"][0]["question"],
             "status": "answered", "user_response": "Use the selected repository"}]}
    monkeypatch.setattr(tools, "_activate", lambda *a: (_ for _ in ()).throw(RuntimeError("launch unavailable")))
    native.observed_after_tool(session_id="chat-a", tool_name="clarify", args=form, result=reply)
    output = json.loads(native.transform_result(session_id="chat-a", tool_name="clarify", args=form, result=reply))
    assert "continuation failed" in output["cpipe_result"][0]["error"]
    assert h.get_task(board, tid).status == "blocked"


@pytest.mark.parametrize("node", ["verified", "handoff"])
@pytest.mark.parametrize("choice", ["once", "deny", None])
def test_publication_approval_is_session_revision_bound_and_once(board, tmp_path, monkeypatch, node, choice):
    from cpipe import action_approval, plan_approval
    tid = waiting(board, tmp_path, node, pr_url="https://github.com/a/b/pull/1")
    monkeypatch.setattr(native, "_session", lambda explicit: explicit or "chat-a")
    monkeypatch.setattr(plan_approval, "_native_prompt", lambda *a: {"resolved": bool(choice), "choice": choice})
    calls = []
    monkeypatch.setattr(tools, "stage", lambda stage, args: calls.append((stage, args)) or {"status": "queued"})
    snap = action_approval.snapshot(board, h.get_task(board, tid), "chat-a")
    assert action_approval.snapshot(board, h.get_task(board, tid), "wrong-session") is None
    args = {"task_id": tid, "approval_key": snap[0]}
    assert "error" in json.loads(action_approval.approve({**args, "approval_key": "stale"}))
    result = json.loads(action_approval.approve(args))
    assert len(calls) == (1 if choice == "once" else 0)
    assert result["status"] == ("queued" if choice == "once" else "waiting")
    assert "already-handled" in json.loads(action_approval.approve(args))["error"]
    if calls:
        assert calls == [("ship", {"task_id": tid, "ready": node == "handoff"})]


def test_review_action_ui_dispatches_verify_not_free_text_build(board, tmp_path, monkeypatch):
    tid = waiting(board, tmp_path, "reviewed", findings=[])
    calls = []
    monkeypatch.setattr(tools, "stage", lambda stage, args: calls.append((stage, args)) or {"stage": stage})
    context = native.before_turn(session_id="chat-a")["context"]
    args = json.loads(context.split("Native clarify arguments:\n")[1])
    assert "Verify" in args["questions"][0]["choices"]
    result = native.after_tool(session_id="chat-a", tool_name="clarify", args=args,
        result={"outcome": "submitted", "responses": [{"question": args["questions"][0]["question"].strip(),
            "status": "answered", "user_response": "Verify"}]})
    assert result == {"stage": "verify"}
    assert calls == [("verify", {"task_id": tid})]
