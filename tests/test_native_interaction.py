"""Native Hermes project context and delivery-scoped clarification; no live deliveries."""
import json
from pathlib import Path

import pytest

pytest.importorskip("hermes_cli.kanban_db", reason="needs a Hermes Agent checkout")

from cpipe import native, tools
from cpipe.runtime import hermes_api as h


@pytest.fixture
def waiting(board, tmp_path):
    def create(session="chat-a", questions=None, node="proposal"):
        ws = tmp_path / f"work-{len(h.list_cards(board))}"
        (ws / ".cpipe").mkdir(parents=True)
        state = {"node": node, "questions": questions or ["Allow floats? Recommended: no."], "resume": "plan"}
        (ws / ".cpipe" / "state.json").write_text(json.dumps(state))
        tid = h.kb.create_task(board, title="opted-in delivery", assignee="cp:hermes", created_by="cpipe",
                               workspace_kind="dir", workspace_path=str(ws), session_id=session)
        with h.kb.write_txn(board):
            board.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (tid,))
        return tid, ws
    return create


def latest_comment(conn, tid, author):
    rows = [row for row in h.comments_after(conn, tid) if row.author == author]
    return rows[-1].body if rows else None


def form(context):
    return json.loads(context["context"].split("Native clarify arguments:\n", 1)[1])


def answer(args, value="No", **extra):
    return json.dumps({"responses": [{"question": q["question"], "user_response": value}
                                     for q in args["questions"]], **extra})


def test_unrelated_chat_and_native_clarification_never_create_a_delivery(board, hermes_home):
    assert native.before_turn(session_id="chat-a") is None
    assert native.after_tool(session_id="chat-a", tool_name="clarify", args={"questions": [{"question": "Tea?"}]},
                             result=answer({"questions": [{"question": "Tea?"}]})) is None
    assert h.list_cards(board) == []
    assert not (hermes_home / "cpipe-interactions.db").exists()


def test_native_answer_resumes_only_existing_same_session_delivery(board, waiting):
    tid, _ = waiting()
    other, _ = waiting(session="chat-b")
    args = form(native.before_turn(session_id="chat-a"))
    assert tid in args["questions"][0]["question"] and other not in json.dumps(args)
    out = native.after_tool(session_id="chat-a", tool_name="clarify", args=args, result=answer(args))
    assert out["task_id"] == tid and out["status"] == "ready"
    assert latest_comment(board, tid, "user") == "Allow floats? Recommended: no.\nAnswer: No"
    assert h.get_task(board, other).status == "review"
    assert len(h.list_cards(board)) == 2
    # This only revises the plan. It is not plan approval, shipping or merging.
    assert latest_comment(board, tid, "cpipe:build") is None
    assert latest_comment(board, tid, "cpipe:ship") is None
    assert native.after_tool(session_id="chat-a", tool_name="clarify", args=args, result=answer(args)) is None


@pytest.mark.parametrize("change", ["wrong-session", "timeout", "empty", "partial", "unrelated", "error", "stale", "archived"])
def test_invalid_or_stale_answers_do_not_resume(board, waiting, change):
    tid, ws = waiting(questions=["First?", "Second?"])
    args = form(native.before_turn(session_id="chat-a"))
    session, result = "chat-a", answer(args)
    if change == "wrong-session":
        session = "chat-b"
    elif change == "timeout":
        result = answer(args, timed_out=True)
    elif change == "empty":
        result = answer(args, value="")
    elif change == "partial":
        result = json.dumps({"responses": json.loads(result)["responses"][:1]})
    elif change == "unrelated":
        args = {"questions": [{"question": "Ordinary chat question?"}]}
        result = answer(args)
    elif change == "error":
        result = json.dumps({"error": "No callback available"})
    elif change == "stale":
        state = json.loads((ws / ".cpipe" / "state.json").read_text())
        state["questions"] = ["A revised decision?"]
        (ws / ".cpipe" / "state.json").write_text(json.dumps(state))
    elif change == "archived":
        h.archive(board, tid)
    assert native.after_tool(session_id=session, tool_name="clarify", args=args, result=result) is None
    assert latest_comment(board, tid, "user") is None
    assert h.get_task(board, tid).status != "ready"


def test_native_answers_are_persistent_batched_and_ambiguous_deliveries_need_selection(board, waiting):
    tid, _ = waiting(questions=[f"Question {i}?" for i in range(7)])
    args = form(native.before_turn(session_id="chat-a"))
    assert len(args["questions"]) == 5
    partial = native.after_tool(session_id="chat-a", tool_name="clarify", args=args, result=answer(args))
    assert partial["remaining"] == 2 and h.get_task(board, tid).status == "review"
    # Read back from disk through a fresh hook invocation, not an in-memory callback.
    rest = form(native.before_turn(session_id="chat-a"))
    assert len(rest["questions"]) == 2 and rest != args
    assert native.after_tool(session_id="chat-a", tool_name="clarify", args=rest,
                             result=answer(rest))["status"] == "ready"
    second, _ = waiting()
    third, _ = waiting()
    context = native.before_turn(session_id="chat-a")
    selection = form(context)
    assert len(selection["questions"][0]["choices"]) == 2
    assert second not in context["context"] and third not in context["context"]
    chosen = native.after_tool(session_id="chat-a", tool_name="clarify", args=selection,
                               result=answer(selection, value=selection["questions"][0]["choices"][0]))
    selected_id = chosen["selected_task_id"]
    assert selected_id in (second, third)
    assert h.get_task(board, selected_id).status == "review"  # selection is not approval/feedback
    assert selected_id in form(native.before_turn(session_id="chat-a"))["questions"][0]["question"]


def test_default_plugin_registers_native_hooks_without_model_delivery_tools():
    class Context:
        def __init__(self):
            self.commands, self.hooks, self.tools = {}, {}, {}
        def register_command(self, name, handler, **kw):
            self.commands[name] = handler
        def register_hook(self, event, callback):
            self.hooks[event] = callback
        def register_tool(self, *, name, **kw):
            self.tools[name] = kw
    ctx = Context()
    tools.register(ctx)
    assert set(ctx.tools) == {"cpipe_execute_intent", "cpipe_approve_plan", "cpipe_approve_action"}
    assert {"pre_llm_call", "post_tool_call"} <= set(ctx.hooks)
    assert "cp-feedback" not in ctx.commands and "cp-project" not in ctx.commands


@pytest.fixture
def native_projects(tmp_path, monkeypatch):
    from hermes_cli import projects_db as p
    connect = p.connect_closing
    monkeypatch.setattr(p, "connect_closing", lambda: connect(db_path=tmp_path / "native-projects.db"))
    return p


def test_native_project_identity_precedes_legacy_repository(native_projects, tmp_path, monkeypatch):
    p = native_projects
    repo, old = tmp_path / "native", tmp_path / "legacy"
    repo.mkdir()
    with p.connect_closing() as conn:
        project = p.create_project(conn, name="My App", slug="my-app", folders=[str(repo)])
    monkeypatch.setattr("cpipe.workspace_prep.projects", lambda: {"my-app": {"repo": str(old)}})
    assert tools.resolve_project(project, tools.settings.load()) == ("my-app", repo)
    assert tools.resolve_project("My App", tools.settings.load()) == ("my-app", repo)


def test_session_project_cwd_is_not_global_active_project(native_projects, tmp_path, monkeypatch):
    p = native_projects
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    with p.connect_closing() as conn:
        p.create_project(conn, name="A", slug="a", folders=[str(a)])
        pb = p.create_project(conn, name="B", slug="b", folders=[str(b)])
        p.set_active(conn, pb)
    monkeypatch.setattr(native, "session_cwd", lambda: a)
    assert tools.resolve_project(None, tools.settings.load()) == ("a", a)
    monkeypatch.setattr(native, "session_cwd", lambda: None)
    assert "question" in tools.resolve_project(None, tools.settings.load())


def test_unknown_project_never_creates_repository_or_native_project(native_projects, tmp_path):
    result = tools.plan({"request": "Fix a button", "project": "unknown-project"})
    assert "clarify" in result["next"] and "task_id" not in result
    assert "/cp-project" not in tools.command("cp-plan", "unknown-project: Fix a button")
    with native_projects.connect_closing() as conn:
        assert native_projects.list_projects(conn) == []
    assert not (tmp_path / "unknown-project").exists()
