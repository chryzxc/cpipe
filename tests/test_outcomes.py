"""The learning loop: how each delivery went, the failures that keep coming back, and lessons on new cards."""
import sys
from pathlib import Path

from cpipe import outcomes, submit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kanban_board import board, card, event, link  # noqa: E402


def delivery(conn, root, project="app", failure="jest: auth.test.js fails: prisma client not generated"):
    """A small delivery whose first verify failed, one fix round, then you reported a bug yourself."""
    d = f"DELIVERY: {root}"
    card(conn, root, "done", title="Add export", project_id=project, workflow_template_id="small",
         current_step_key="build", completed_at=1)
    event(conn, root, "changes_requested")
    card(conn, f"{root}v", "done", body=d, current_step_key="verify", result=f"VERDICT: FAIL abc1234\n{failure}")
    card(conn, f"{root}f", "done", body=d, current_step_key="fix")
    link(conn, f"{root}v", f"{root}f")
    card(conn, f"{root}u", "todo", body=d, current_step_key="fix")  # fix_of: parent is the build
    link(conn, root, f"{root}u")


def test_a_delivery_is_its_first_card_and_every_card_naming_it(tmp_path):
    conn = board(tmp_path / "kanban.db")
    delivery(conn, "t_a")
    card(conn, "t_old", "done", title="Before tagging")  # untagged: not a delivery
    [row] = outcomes.deliveries(conn)
    assert (row["id"], row["cards"], row["open"]) == ("t_a", 4, True)
    assert (row["review_rounds"], row["fix_rounds"], row["user_fixes"], row["verify_fails"]) == (1, 1, 1, 1)
    assert row["failures"] == ["jest: auth.test.js fails: prisma client not generated"]


def test_a_failure_in_two_deliveries_of_a_project_is_a_lesson_candidate(tmp_path):
    conn = board(tmp_path / "kanban.db")
    delivery(conn, "t_a")
    delivery(conn, "t_b", failure="jest: auth.test.js fails: prisma client not generated")
    delivery(conn, "t_c", project="other")
    rows = outcomes.deliveries(conn)
    assert [(p, n) for p, _, n, _ in outcomes.recurring(rows)] == [("app", 2)]
    text = outcomes.report(rows)
    assert "## app: 2 deliveries" in text and "LESSON CANDIDATE (2 deliveries" in text
    assert "you sent fixes for: t_a" in text


def test_approved_lessons_reach_the_projects_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path)
    (tmp_path / "delivery").mkdir()
    (tmp_path / "delivery" / "lessons.yaml").write_text(
        '"*":\n  - Say why an error happened\napp:\n'
        + "".join(f"  - {{text: lesson {n}, added: 2026-10-05}}\n" for n in range(12)))
    brief = submit._conventions("app")
    assert brief.startswith("LESSONS") and "lesson 11" in brief and "Say why" not in brief  # newest 10 win
    assert submit._conventions("other") == "LESSONS (learned from past deliveries; follow them):\n- Say why an error happened"
    (tmp_path / "delivery" / "lessons.yaml").write_text(": not yaml [")
    assert submit._conventions("app") == ""  # a broken file never blocks a submission
