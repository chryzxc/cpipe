"""A headless coordinator's clarify becomes a queued question on a blocked card instead of a guess."""
import json
import sqlite3
import subprocess

from cpipe import headless_clarify as hc, submit

ASK = {"question": "t_0000aaaa: fix the gap or accept the risk?", "choices": ["Fix (Recommended)", "Accept"]}


def setup(tmp_path, monkeypatch, status="blocked", headless=True, block_rc=0):
    db = tmp_path / "kanban.db"
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE IF EXISTS tasks")
        conn.execute("CREATE TABLE tasks (id TEXT, status TEXT)")
        conn.execute("INSERT INTO tasks VALUES ('t_0000aaaa', ?)", (status,))
    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path)
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    if headless:
        monkeypatch.setenv(hc.HEADLESS_ENV, str(tmp_path / "turn.json"))
    else:
        monkeypatch.delenv(hc.HEADLESS_ENV, raising=False)
    calls = []
    monkeypatch.setattr(submit, "_hermes", lambda *a: calls.append(a) or subprocess.CompletedProcess(
        a, block_rc if a[1] == "block" else 0))
    monkeypatch.setattr(submit, "_journal", lambda *a, **k: None)
    return calls


def test_headless_question_is_queued_and_card_blocked(tmp_path, monkeypatch):
    calls = setup(tmp_path, monkeypatch)
    verdict = hc.gate(tool_name="clarify", args=ASK, session_id="bot")
    assert verdict["action"] == "block" and "Do NOT pick" in verdict["message"]
    saved = json.loads((tmp_path / "delivery/questions/t_0000aaaa.json").read_text())
    assert saved["session_id"] == "bot" and saved["questions"][0]["choices"] == ASK["choices"]
    assert calls == [("kanban", "block", "--kind", "needs_input", "t_0000aaaa", f"QUESTION: {ASK['question']}")]


def test_live_turns_and_other_tools_untouched(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, headless=False)
    assert hc.gate(tool_name="clarify", args=ASK) is None
    setup(tmp_path, monkeypatch)
    assert hc.gate(tool_name="terminal", args={"command": "ls"}) is None


def test_question_without_card_or_on_running_card_is_not_queued(tmp_path, monkeypatch):
    calls = setup(tmp_path, monkeypatch, status="running")
    assert "Name the card" in hc.gate(tool_name="clarify", args={"question": "which env?"})["message"]
    assert "still running" in hc.gate(tool_name="clarify", args=ASK)["message"]
    assert calls == [] and not (tmp_path / "delivery/questions").exists()


def test_card_that_cannot_block_gets_the_question_as_a_comment(tmp_path, monkeypatch):
    calls = setup(tmp_path, monkeypatch, status="todo", block_rc=1)
    verdict = hc.gate(tool_name="clarify", args=ASK, session_id="bot")
    assert "has a comment with" in verdict["message"]
    assert calls[-1] == ("kanban", "comment", "t_0000aaaa", f"QUESTION for the user: {ASK['question']}")
