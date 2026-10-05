"""Flows: each card is tagged with its flow and step, and everything that routes cards matches the tag."""
import json
import sys
from pathlib import Path

from cpipe import checks, submit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kanban_board import board, card  # noqa: E402
from test_delivery_submit import arg, fake_board  # noqa: E402


def tagged_board(tmp_path, monkeypatch):
    """fake_board whose creates land on a fixture board, so _tag has rows to update."""
    calls = fake_board(tmp_path, monkeypatch)
    conn = board(tmp_path / "kanban.db")
    conn.commit()
    monkeypatch.setattr(submit, "_db", lambda: tmp_path / "kanban.db")
    monkeypatch.setattr(submit, "_journal", lambda *a, **k: None)
    fake = submit._hermes

    def create(*argv):
        out = fake(*argv)
        card(conn, f"t_{len(calls)}", "todo", title=argv[2])
        conn.commit()
        return out
    monkeypatch.setattr(submit, "_hermes", create)
    return calls, conn


def test_a_large_submission_is_a_tagged_chain(tmp_path, monkeypatch):
    calls, conn = tagged_board(tmp_path, monkeypatch)
    result = json.loads(submit.submit({"title": "Add export", "request": "CSV export", "project": "p", "size": "large"}))
    assert result["cards"] == {"map": "t_1", "plan": "t_2", "build": "t_3", "verify": "t_4"}
    assert [arg(c, "--parent") if "--parent" in c else None for c in calls] == [None, "t_1", "t_2", "t_3"]
    tags = conn.execute("SELECT id, workflow_template_id, current_step_key FROM tasks ORDER BY id").fetchall()
    assert [tuple(t) for t in tags] == [("t_1", "large", "map"), ("t_2", "large", "plan"),
                                        ("t_3", "large", "build"), ("t_4", "large", "verify")]


def test_parallel_steps_share_the_parent(tmp_path, monkeypatch):
    calls, conn = tagged_board(tmp_path, monkeypatch)
    chain, error = submit.run_flow("design", "Settings", "Redo it", "", parent="t_x", refs="none", code="/r", key="d_1")
    assert error is None and list(chain) == ["A", "B", "C"]
    assert {arg(c, "--parent") for c in calls} == {"t_x"} and "--project" not in calls[0]


def test_steps_match_the_tag_and_fall_back_to_the_title(tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_old", "todo", title="Verify: Add export")  # made before tagging
    card(conn, "t_new", "todo", title="Recheck the export", current_step_key="verify")
    card(conn, "t_spec", "todo", title="Decide the OTP design")
    steps = dict(conn.execute(f"SELECT id, {submit.step_sql()} FROM tasks").fetchall())
    assert steps == {"t_old": "verify", "t_new": "verify", "t_spec": "build"}


def test_the_graph_has_no_dead_ends():
    steps = {step.key for flow in submit.FLOWS.values() for step in flow}
    assert steps == {step for step, _ in submit.EDGES}  # every step has edges, every edge a step
    assert all((step, "done") in submit.EDGES for step in steps - {"verify"})
    elsewhere = {"next", "user_pick", "ask_user", "start_verify"}  # Hermes, the pick tool, the gate, release
    assert set(submit.EDGES.values()) <= elsewhere | set(checks.ACTIONS)
    assert submit.EDGES[("verify", "fail_limit")] == "ask_user"  # the fix loop is bounded


def test_later_cards_name_their_delivery(tmp_path, monkeypatch):
    calls, conn = tagged_board(tmp_path, monkeypatch)
    submit.submit({"title": "Add export", "request": "CSV export", "project": "p", "size": "large"})
    bodies = [arg(c, "--body") for c in calls]
    assert "DELIVERY:" not in bodies[0] and all(b.rstrip().endswith("DELIVERY: t_1") for b in bodies[1:])
