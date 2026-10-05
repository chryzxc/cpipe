"""UI work with design=true: three designers mock up directions in parallel; the build waits for the user's pick."""
import json
import os
import sys
from pathlib import Path

from cpipe import submit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kanban_board import board, card  # noqa: E402
from test_delivery_submit import _built_card, arg, fake_board  # noqa: E402


def designed(tmp_path, monkeypatch, **extra):
    calls = fake_board(tmp_path, monkeypatch)
    monkeypatch.setattr(submit, "_project_repo", lambda project: "/repo")
    monkeypatch.setattr(submit, "_journal", lambda *a, **k: None)
    result = json.loads(submit.submit({"title": "Floating chat", "request": "A chat bubble on every page",
                                       "project": "my-app", "design": True, "design_refs": "like Intercom",
                                       "live_qa": True, "qa_login": {"username": "qa", "password": "pw"}, **extra}))
    return calls, result


def test_design_mocks_up_three_directions_and_builds_nothing(tmp_path, monkeypatch):
    calls, result = designed(tmp_path, monkeypatch)
    assert result["ok"] and result["cards"] == {"A": "t_1", "B": "t_2", "C": "t_3"}
    assert [c[2] for c in calls] == ["Design A: Floating chat", "Design B: Floating chat", "Design C: Floating chat"]
    for letter, c in zip("ABC", calls):
        body = arg(c, "--body")
        assert arg(c, "--workspace") == "scratch" and "--project" not in c  # Hermes keeps named files from scratch only
        assert arg(c, "--assignee") == "forge"  # no designer mapped: the implementer designs
        assert f"design-{letter}.html" in body and submit.DESIGN_DIRECTIONS[letter] in body and "{" not in body
        assert "like Intercom" in body and "/repo (look, never change" in body
        assert f"DESIGN SET: {result['design']}" in body
    pending = tmp_path / "delivery" / "designs" / f"{result['design']}.json"
    assert oct(os.stat(pending).st_mode & 0o777) == "0o600"  # holds the QA test account
    assert "design_pick" in result["next"] and result["design"] in result["next"]


def test_pick_builds_with_the_picked_design_on_every_card(tmp_path, monkeypatch):
    calls, result = designed(tmp_path, monkeypatch)
    conn = board(tmp_path / "kanban.db")
    conn.execute("CREATE TABLE task_attachments (id INTEGER PRIMARY KEY, task_id TEXT, stored_path TEXT)")
    for tid, status in (("t_1", "done"), ("t_2", "done"), ("t_3", "running")):
        card(conn, tid, status, result=f"DESIGN {tid}: idea" if status == "done" else None,
             body=f"DESIGN SET: {result['design']}")
    conn.execute("INSERT INTO task_attachments (task_id, stored_path) VALUES ('t_1', '/att/t_1/design-A.html'), "
                 "('t_2', '/att/t_2/design-B.html')")
    conn.commit()
    monkeypatch.setattr(submit, "_db", lambda: tmp_path / "kanban.db")
    monkeypatch.setattr(submit, "_task_row", lambda tid: {"body": f"DESIGN SET: {result['design']}"})
    del calls[:]
    assert "not done" in json.loads(submit.design_pick({"design": "t_1", "choice": "C"}))["error"]
    picked = json.loads(submit.design_pick({"design": "t_2", "choice": "A", "notes": "with B's header"}))
    assert picked["ok"] and picked["picked"] == "A" and picked["cards"] == {"build": "t_1", "verify": "t_2"}  # fake ids restart
    build, verify = calls
    for c in (build, verify):
        body = arg(c, "--body")
        assert "PICKED DESIGN A" in body and "/att/t_1/design-A.html" in body and "with B's header" in body
        assert "/att/t_2/design-B.html" in body  # the notes name B: its mockup comes along
    assert build[2] == "Floating chat" and "LIVE QA" in arg(verify, "--body")
    assert "already picked" in json.loads(submit.design_pick({"design": result["design"], "choice": "B"}))["error"]


def test_design_on_a_fix_reads_the_pr_worktree_then_picks_into_the_fix(tmp_path, monkeypatch):
    calls = fake_board(tmp_path, monkeypatch)
    _built_card(monkeypatch)
    monkeypatch.setattr(submit, "_journal", lambda *a, **k: None)
    result = json.loads(submit.submit({"fix_of": "t_b", "request": "the panel looks cramped", "design": True}))
    assert [c[2] for c in calls] == ["Design A: Gate SMS", "Design B: Gate SMS", "Design C: Gate SMS"]
    body = arg(calls[0], "--body")
    assert "/repo/.worktrees/t1 (look" in body and "the panel looks cramped" in body and "Gate SMS on consent" in body
    monkeypatch.setattr(submit, "_design_output", lambda tid: (["/att/a.html"], "DESIGN A: roomy", "done"))
    del calls[:]
    picked = json.loads(submit.design_pick({"design": result["design"], "choice": "a"}))
    [fix] = calls
    assert picked["ok"] and fix[2] == "Fix 1: Gate SMS" and arg(fix, "--workspace") == "dir:/repo/.worktrees/t1"
    assert "PICKED DESIGN A" in arg(fix, "--body") and "/att/a.html" in arg(fix, "--body")
