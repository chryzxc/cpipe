"""No gate deadlock: every normal handoff passes all pre_tool_call gates together, so a new gate cannot wedge
the flow it guards (a bot left with no allowed way to hand its card on)."""
import subprocess

from cpipe import card_gate, headless_clarify, quality, review_gate, status, submit, workspace_prep

GATES = (review_gate.gate, headless_clarify.gate, card_gate.gate, status.turn_budget)
PLAN = "\n".join(f"{h}\nnone, see the code." for h in submit.PLAN_HEADINGS) + "\n" + "detail " * 100


def passes(tool_name, args):
    ids = {"session_id": "s", "turn_id": "u", "task_id": "t_1", "tool_call_id": "c"}
    return [v for g in GATES if (v := g(tool_name=tool_name, args=args, **ids))]


def board(tmp_path, monkeypatch, assignee, body):
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    remote, ws = tmp_path / "remote.git", tmp_path / "ws"
    if not ws.exists():  # a clean worktree, pushed
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "clone", "-q", str(remote), str(ws)], check=True, capture_output=True)
        (ws / "a.js").write_text("x\n")
        for cmd in (["add", "."], ["commit", "-qm", "a"], ["push", "-q", "-u", "origin", "HEAD"]):
            subprocess.run([*git, "-C", str(ws), *cmd], check=True, capture_output=True)
    (tmp_path / "roster.yaml").write_text("roles:\n  implementer: forge\n  reviewer: sentry\n  verifier: sentinel\n"
                                          "  planner: archon\n")
    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    monkeypatch.setattr(review_gate, "_task", lambda tid: {"assignee": assignee, "body": body,
                                                           "workspace_path": str(ws), "project_id": "p"})
    monkeypatch.setattr(review_gate, "route_verify", lambda *a: None)
    monkeypatch.setattr(workspace_prep, "card_profile", lambda *a, **k: {"base_branch": "main"})
    monkeypatch.setattr(quality, "_blocked", {})
    return ws


def test_every_normal_handoff_passes_every_gate(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, "forge", review_gate.MARKER)
    assert passes("kanban_request_review", {"task_id": "t_1", "summary": "PR; tests green"}) == []
    assert passes("kanban_block", {"task_id": "t_1", "reason": "Should exports cap at 200 or 1,000 rows?"}) == []
    board(tmp_path, monkeypatch, "sentry", review_gate.MARKER)
    assert passes("kanban_complete", {"task_id": "t_1", "summary": "approved"}) == []
    assert passes("kanban_request_changes", {"task_id": "t_1", "reason": "missing test"}) == []
    board(tmp_path, monkeypatch, "sentinel", review_gate.VERIFY_MARKER)
    assert passes("kanban_complete", {"task_id": "t_1", "result": "VERDICT: PASS abc1234\nnpm test: ok"}) == []
    board(tmp_path, monkeypatch, "archon", review_gate.PLAN_MARKER)
    assert passes("kanban_complete", {"task_id": "t_1", "result": PLAN}) == []
