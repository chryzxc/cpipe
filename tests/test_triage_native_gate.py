"""The board breaker must not hide a valid native clarification."""
import json

from cpipe import native
from cpipe.runtime import hermes_api as h


def test_triage_delivery_still_offers_native_questions(board, tmp_path, monkeypatch):
    ws = tmp_path / "work"
    (ws / ".cpipe").mkdir(parents=True)
    (ws / ".cpipe" / "state.json").write_text(json.dumps({"node": "ask", "resume": "plan"}))
    tid = h.kb.create_task(board, title="Plan endpoint fix", assignee="archon", created_by="cpipe",
                          workspace_kind="dir", workspace_path=str(ws), session_id="chat-a")
    with h.kb.write_txn(board):
        board.execute("UPDATE tasks SET status='triage' WHERE id=?", (tid,))
    monkeypatch.setattr(h, "latest_summary", lambda *a: "Which existing access policy should apply?")
    task = h.get_task(board, tid)
    snapshot = native._snapshot(board, task, "chat-a")
    assert snapshot is not None
    assert snapshot[1] == ["Which existing access policy should apply?"]
    assert native._snapshot(board, task, "other-session") is None


def test_answering_visible_triage_question_resumes_without_plan_approval(board, tmp_path):
    from cpipe import tools
    ws = tmp_path / "work"
    (ws / ".cpipe").mkdir(parents=True)
    (ws / ".cpipe" / "state.json").write_text(json.dumps({"node": "ask", "resume": "plan"}))
    tid = h.kb.create_task(board, title="Plan endpoint fix", assignee="archon", created_by="cpipe",
                          workspace_kind="dir", workspace_path=str(ws), session_id="chat-a", triage=True)
    result = tools.feedback({"task_id": tid, "text": "Keep the existing access policy"})
    assert result["status"] == "ready" and result["dispatch_scheduled"]
    comments = h.comments_after(board, tid)
    assert any(c.author == "user" and c.body == "Keep the existing access policy" for c in comments)
    assert not any(c.author in ("cpipe:accept", "cpipe:ship") for c in comments)


def test_triage_setup_does_not_offer_approval(board, tmp_path):
    ws = tmp_path / "work"
    (ws / ".cpipe").mkdir(parents=True)
    (ws / ".cpipe" / "state.json").write_text(json.dumps({"node": "setup"}))
    tid = h.kb.create_task(board, title="Launch failed", assignee="archon", created_by="cpipe",
                          workspace_kind="dir", workspace_path=str(ws), session_id="chat-a")
    with h.kb.write_txn(board):
        board.execute("UPDATE tasks SET status='triage' WHERE id=?", (tid,))
    assert native._snapshot(board, h.get_task(board, tid), "chat-a") is None
