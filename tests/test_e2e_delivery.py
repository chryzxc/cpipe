"""End to end against the real `hermes kanban` CLI on a throwaway board DB.

HERMES_KANBAN_DB points the CLI at a temp file the gateway never dispatches, so no worker runs and no
model is called. Covers submit -> Plan/Build/Verify chain, the review and plan gates, and a headless
clarify blocking its card. Skipped when `hermes` is not installed. Run alone:
`.venv/bin/python -m pytest -m e2e` (`uv run` rewrites uv.lock, and hermes rebuilds whenever any file
in this plugin changes).
"""
import json
import shutil
import sqlite3
import subprocess

import pytest

from cpipe import headless_clarify, review_gate, submit

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not shutil.which("hermes"), reason="hermes CLI not installed")]


def cards(db):
    with sqlite3.connect(db) as conn:
        return {r[0]: r[1:] for r in conn.execute("SELECT id, title, status, assignee FROM tasks")}


def test_delivery_flow(tmp_path, monkeypatch):
    db = tmp_path / "kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    monkeypatch.delenv("HERMES_HOME")  # the CLI needs its installed home, not the harness's blank one
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setattr(submit, "_journal", lambda *a, **k: None)  # keep the real journal clean
    # hermes hashes this plugin's files: after an edit its next call rebuilds for minutes. Let that
    # finish here; a kill at the CLI's 120s timeout leaves the rebuild pending for every later call.
    subprocess.run(["hermes", "kanban", "list"], capture_output=True, timeout=1200)

    out = json.loads(submit.submit({"title": "E2E probe", "request": "Add one line to README",
                                    "project": "cpipe", "size": "small"}))
    assert out["ok"], out
    chain = out["cards"]
    board = cards(db)
    assert set(chain) == {"plan", "build", "verify"}
    assert board[chain["plan"]][:2] == ("Plan: E2E probe", "ready")
    assert board[chain["build"]][1:] == ("todo", submit._role("implementer"))
    assert board[chain["verify"]][0] == "Verify: E2E probe"

    thin = review_gate.gate(tool_name="kanban_complete", args={"task_id": chain["plan"], "result": "do it"})
    assert thin["action"] == "block" and "PLAN FORMAT" in thin["message"]
    plan = "\n".join(f"{h}: none" + " filler" * 30 for h in submit.PLAN_HEADINGS)
    moved = review_gate.gate(tool_name="kanban_complete", args={"task_id": chain["plan"], "summary": plan})
    assert moved == {"action": "modify", "args": {"result": plan}}

    self_done = review_gate.gate(tool_name="terminal", args={"command": f"hermes kanban complete {chain['build']}"})
    assert self_done["action"] == "block" and "request-review" in self_done["message"]

    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path)  # question files land in the temp home
    monkeypatch.setenv(headless_clarify.HEADLESS_ENV, str(tmp_path / "turn.json"))
    ask = {"question": f"{chain['plan']}: ship now or wait?", "choices": ["Ship", "Wait"]}
    queued = headless_clarify.gate(tool_name="clarify", args=ask, session_id="bot-chat")
    assert queued["action"] == "block" and "Do NOT pick" in queued["message"]
    saved = json.loads((tmp_path / f"delivery/questions/{chain['plan']}.json").read_text())
    assert saved["session_id"] == "bot-chat" and saved["questions"][0]["choices"] == ["Ship", "Wait"]
    assert cards(db)[chain["plan"]][1] == "blocked"
    waiting = headless_clarify.gate(tool_name="clarify", args={**ask, "question": f"{chain['build']}: which?"})
    assert "has a comment with" in waiting["message"]  # a todo card cannot block
