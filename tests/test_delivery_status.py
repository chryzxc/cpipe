"""The chat answers status from the board: tool verdicts, watches, notices, and claim checks."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kanban_board import board, card, subscribe  # noqa: E402

from cpipe import status  # noqa: E402


def test_status_reports_the_monitor_verdict(tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_aaaaaa1", "ready", minutes_ago=45)
    card(conn, "t_aaaaaa2", "blocked", minutes_ago=5, block_kind="needs_input")
    subscribe(conn, "t_aaaaaa2")
    result = status.status(home=tmp_path)
    rows = {r["card"]: r for r in result["cards"]}
    assert rows["t_aaaaaa1"]["verdict"] == "STUCK(UNKNOWN_STALL)" and rows["t_aaaaaa1"]["next"]
    assert rows["t_aaaaaa2"]["verdict"].startswith("WAITING(human")
    assert result["cards"][0]["card"] == "t_aaaaaa1"  # stuck first
    assert status.status("t_nope", home=tmp_path)["ok"] is False


def test_watch_validates_and_persists(tmp_path):
    assert not status.watch("t_abcdef1", "pr", "rm -rf /", home=tmp_path)["ok"]
    assert status.watch("t_abcdef1", "ci", "https://github.com/o/r/pull/7", hours=2, home=tmp_path)["ok"]
    saved = json.loads((tmp_path / "state" / "delivery-watches.json").read_text())
    assert saved["t_abcdef1"]["ref"].endswith("/pull/7") and saved["t_abcdef1"]["until"] > time.time()


def test_notices_are_delivered_once_to_their_chat(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "delivery-notices.json").write_text(json.dumps(
        {"chat-1": [{"card": "t_x", "title": "Fix", "text": "ORPHAN_REVIEW: …; next: …"}]}))
    monkeypatch.setattr(status, "_chat_id", lambda: "chat-1")
    ctx = status.chat_context(session_id="s", user_message="hi", home=tmp_path)
    assert "ORPHAN_REVIEW" in ctx["context"]
    assert status.chat_context(session_id="s", user_message="hi", home=tmp_path) is None


def test_progress_claim_the_board_contradicts_is_corrected(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_bbbbbb1", "ready", minutes_ago=45)
    status.claim_check(session_id="s2", assistant_response="t_bbbbbb1 will resume shortly.", home=tmp_path)
    ctx = status.chat_context(session_id="s2", user_message="any update?", home=tmp_path)
    assert "STUCK(UNKNOWN_STALL)" in ctx["context"] and "delivery_status" in ctx["context"]
    kinds = [json.loads(l)["kind"] for l in (tmp_path / "logs" / "delivery-journal.jsonl").read_text().splitlines()]
    assert "claim.unsupported" in kinds and "nudge" in kinds


def test_approval_prompt_in_a_worker_blocks_the_card(monkeypatch):
    calls = []
    status.approval_requested(command="npm publish", run=lambda *a: calls.append(a))
    assert calls == []  # not a worker session: journal only
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_abcdef1")
    status.approval_requested(command="npm publish", run=lambda *a: calls.append(a))
    assert calls[0][:5] == ("kanban", "block", "t_abcdef1", "--kind", "needs_input")
    assert calls[0][5].startswith("APPROVAL_NEEDED: npm publish")
