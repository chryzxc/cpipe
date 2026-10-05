"""Verify runs beside review in its own checkout; the join marks the PR ready or re-verifies what changed."""
import json
import subprocess
import sys
from pathlib import Path

from cpipe import checks, review_gate, submit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kanban_board import board, card, event, link  # noqa: E402


class FakeBoard:
    def __init__(self, conn):
        self.conn, self.comments = conn, []

    def comment(self, tid, text):
        self.comments.append((tid, text))

    def unlink(self, parent, child):
        self.conn.execute("DELETE FROM task_links WHERE parent_id = ? AND child_id = ?", (parent, child))

    def assign(self, tid, who):
        pass


def repo(tmp_path):
    wt = tmp_path / "app"
    wt.mkdir(parents=True)
    git = lambda *a: subprocess.run(["git", "-C", str(wt), *a], check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "one")
    return str(wt), git


def delivery(tmp_path, monkeypatch, build_status="review"):
    monkeypatch.setattr(submit, "_journal", lambda *a, **k: None)
    monkeypatch.setattr(review_gate, "route_verify", lambda *a, **k: None)
    wt, git = repo(tmp_path)
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_b", build_status, title="Add export", body=f"{review_gate.MARKER}\nREQUEST:\nAdd export",
         workspace_path=wt, project_id="app")
    event(conn, "t_b", "review_requested", summary="PR https://github.com/o/r/pull/7; PINNED a.test.js")
    card(conn, "t_v", "todo", title="Verify: Add export", body=submit.VERIFY_BRIEF.format(request="Add export")
         + "BUILD CARD: t_b\n", created_by="delivery_submit")
    link(conn, "t_b", "t_v")
    return conn, wt, git


def test_verify_starts_beside_review_in_its_own_checkout(tmp_path, monkeypatch):
    conn, wt, git = delivery(tmp_path, monkeypatch)
    fake, root, state = FakeBoard(conn), tmp_path / "ws", {}
    assert checks.release(conn, fake, root, state) == ["t_v"]
    head = git("rev-parse", "HEAD")
    checkout = subprocess.run(["git", "-C", str(root / "t_v"), "rev-parse", "HEAD"], capture_output=True, text=True)
    assert checkout.stdout.strip() == head and state["released"] == {"t_v": head}
    assert conn.execute("SELECT count(*) FROM task_links").fetchone()[0] == 0  # Hermes may now start it
    (vid, note), (bid, _) = fake.comments
    assert (vid, bid) == ("t_v", "t_b") and "pull/7" in note and head in note
    assert checks.build_of(conn, "t_v")[0] == "t_b"  # the BUILD CARD line keeps the relation
    assert checks.release(conn, fake, root, state) == []  # nothing left to release


def finish(conn, tid, status, result=None):
    conn.execute("UPDATE tasks SET status = ?, result = ?, completed_at = strftime('%s','now') WHERE id = ?",
                 (status, result, tid))


def joined(tmp_path, monkeypatch, verdict, build_status="done", fix_child=False):
    conn, wt, git = delivery(tmp_path, monkeypatch)
    conn.execute("DELETE FROM task_links")
    head = git("rev-parse", "HEAD")
    sha = head if verdict.endswith("HEAD") else "0123456789abc"
    finish(conn, "t_v", "done", f"VERDICT: {verdict.split()[0]} {sha}\njest ok")
    finish(conn, "t_b", build_status)
    if fix_child:
        card(conn, "t_f", "todo", title="Fix 1: Add export")
        link(conn, "t_v", "t_f")
    ready, deltas, state = [], [], {"since": 0}
    acts = checks.join(conn, tmp_path / "ws", state, ready=ready.append,
                       delta=lambda *a: deltas.append(a[3:]))
    return acts, ready, deltas, state, conn


def test_pass_on_the_approved_commit_marks_the_pr_ready(tmp_path, monkeypatch):
    acts, ready, deltas, state, _ = joined(tmp_path, monkeypatch, "PASS HEAD")
    assert acts == [("t_v", "ready")] and ready == [str(tmp_path / "app")] and deltas == []
    assert state["joined"] == ["t_v"]


def test_pass_before_the_last_review_round_reverifies_the_delta(tmp_path, monkeypatch):
    acts, ready, deltas, *_ = joined(tmp_path, monkeypatch, "PASS old")
    assert acts == [("t_v", "delta")] and ready == [] and deltas[0] == ("PASS", "0123456789abc")


def test_failure_fixed_in_review_reverifies_but_a_fix_card_is_left_alone(tmp_path, monkeypatch):
    assert joined(tmp_path, monkeypatch, "FAIL old")[0] == [("t_v", "delta")]
    acts, ready, deltas, *_ = joined(tmp_path / "2", monkeypatch, "FAIL HEAD", fix_child=True)
    assert acts == [("t_v", "none")] and ready == deltas == []


def test_join_waits_for_the_review_and_ignores_old_verifies(tmp_path, monkeypatch):
    acts, ready, deltas, state, conn = joined(tmp_path, monkeypatch, "PASS HEAD", build_status="review")
    assert acts == [] and "t_v" not in state["joined"]
    finish(conn, "t_b", "done")
    assert checks.join(conn, tmp_path / "ws", {"since": 9e12}) == []  # finished before cpipe started joining


def test_verify_must_report_a_verdict_and_never_marks_the_pr_ready(tmp_path, monkeypatch):
    conn, wt, _ = delivery(tmp_path, monkeypatch)
    conn.commit()
    monkeypatch.setattr(submit, "_db", lambda: tmp_path / "kanban.db")
    monkeypatch.setattr(review_gate, "_task", lambda tid: conn.execute(
        "SELECT assignee, body, workspace_path, project_id FROM tasks WHERE id = ?", (tid,)).fetchone())
    blocked = review_gate.gate("kanban_complete", {"task_id": "t_v", "result": "all green"})
    assert blocked["action"] == "block" and "VERDICT" in blocked["message"]
    assert review_gate.gate("kanban_complete", {"task_id": "t_v", "result": "VERDICT: PASS abc1234\nok"}) is None
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_b")  # the reviewer, while a verify checks its card
    assert review_gate.gate("terminal", {"command": "gh pr ready https://github.com/o/r/pull/7"})["action"] == "block"
    assert review_gate.gate("terminal", {"command": "gh pr ready --undo 7"}) is None


def test_verify_failure_during_review_joins_the_review_round(tmp_path, monkeypatch):
    conn, *_ = delivery(tmp_path, monkeypatch)
    conn.commit()
    monkeypatch.setattr(submit, "_db", lambda: tmp_path / "kanban.db")
    calls = []
    monkeypatch.setattr(submit, "_hermes", lambda *argv: calls.append(argv) or type("P", (), {"returncode": 0}))
    result = json.loads(submit.verify_failed({"task_id": "t_v", "failures": "jest: a.test.js fails"}))
    assert result["ok"] and result["routed_to"] == "t_b"
    assert calls[0][:3] == ("kanban", "comment", "t_b") and "jest: a.test.js fails" in calls[0][3]


def test_a_verify_card_takes_its_project_from_the_card_it_checks(tmp_path, monkeypatch):
    from cpipe import workspace_prep
    conn, *_ = delivery(tmp_path, monkeypatch)
    conn.commit()
    monkeypatch.setattr(workspace_prep, "project_profile", lambda project, home=None: {"base_branch": "dev"} if project == "app" else {})
    assert workspace_prep.card_profile("t_v", tmp_path / "kanban.db") == {"base_branch": "dev", "project": "app"}
    conn.execute("DELETE FROM task_links")  # released: only the BUILD CARD line is left
    conn.commit()
    assert workspace_prep.card_profile("t_v", tmp_path / "kanban.db")["project"] == "app"
