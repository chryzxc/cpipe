"""The implementer cannot complete its own reviewed card; approvals route platform diffs to the release engineer."""
from cpipe import review_gate, submit


def setup(tmp_path, monkeypatch, assignee, body=review_gate.MARKER):
    (tmp_path / "roster.yaml").write_text("roles:\n  implementer: forge\n  reviewer: sentry\n  release_engineer: aegis\n")
    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path)
    row = {"assignee": assignee, "body": body, "workspace_path": "/wt", "project_id": "p"}
    monkeypatch.setattr(review_gate, "_task", lambda tid: row)
    routed = []
    monkeypatch.setattr(review_gate, "route_verify", lambda *a: routed.append(a))
    return routed


def test_implementer_self_complete_is_blocked(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, "forge")
    for name, args in (("kanban_complete", {"task_id": "t_1"}),
                       ("terminal", {"command": "hermes kanban complete t_1a2b --summary done"})):
        verdict = review_gate.gate(tool_name=name, args=args)
        assert verdict["action"] == "block" and "request-review" in verdict["message"]


def test_reviewer_approval_passes_and_routes_verify(tmp_path, monkeypatch):
    routed = setup(tmp_path, monkeypatch, "sentry")
    assert review_gate.gate(tool_name="kanban_complete", args={"task_id": "t_1"}) is None
    assert routed == [("t_1", "/wt", "p")]


def test_implementer_cannot_block_to_ask_for_commit_approval(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, "forge", body="hand-written card")
    for name, args in (
            ("kanban_block", {"task_id": "t_1", "reason": "Staged and green; please authorize the proposed commit"}),
            ("kanban_block", {"task_id": "t_1", "reason": "COMMIT_READY: one-file staged candidate"}),
            ("terminal", {"command": "hermes kanban block t_1a2b 'waiting for coordinator validation before commit'"})):
        verdict = review_gate.gate(tool_name=name, args=args)
        assert verdict["action"] == "block" and "request-review" in verdict["message"]
    real = {"task_id": "t_1", "reason": "Should staff exports cap at 200 or 1,000 rows?"}
    assert review_gate.gate(tool_name="kanban_block", args=real) is None
    setup(tmp_path, monkeypatch, "sentry")
    assert review_gate.gate(tool_name="kanban_block", args={"task_id": "t_1", "reason": "COMMIT_READY"}) is None


def test_other_cards_and_tools_untouched(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, "forge", body="VERIFY the reviewed change")
    assert review_gate.gate(tool_name="kanban_complete", args={"task_id": "t_1"}) is None
    assert review_gate.gate(tool_name="terminal", args={"command": "ls"}) is None


def test_platform_paths():
    hit = ["client/package.json", ".github/workflows/ci.yml", "server/migrations/001.js", "Dockerfile",
           "pnpm-lock.yaml", "deploy/nginx.conf"]
    miss = ["client/src/views/Userprofile.vue", "server/routes/users.js", "docs/deploy-notes.md.txt"]
    assert all(review_gate.PLATFORM_PATH.search(p) for p in hit)
    assert not any(review_gate.PLATFORM_PATH.search(p) for p in miss)


def _plan_card(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, "archon", body=review_gate.PLAN_MARKER + "...")


FULL_PLAN = "\n".join(f"{h}: none" for h in submit.PLAN_HEADINGS) + "\n" + "detail " * 100


def test_plan_needs_every_format_heading(tmp_path, monkeypatch):
    _plan_card(tmp_path, monkeypatch)
    short = review_gate.gate(tool_name="kanban_complete", args={"summary": "Plan is in the summary above."})
    assert short["action"] == "block" and "ENTRY POINTS" in short["message"]
    no_contracts = FULL_PLAN.replace("CONTRACTS: none\n", "")
    assert "CONTRACTS" in review_gate.gate(tool_name="kanban_complete", args={"result": no_contracts})["message"]
    assert review_gate.gate(tool_name="kanban_complete", args={"result": FULL_PLAN}) is None


def test_plan_left_in_summary_is_copied_to_result(tmp_path, monkeypatch):
    _plan_card(tmp_path, monkeypatch)
    verdict = review_gate.gate(tool_name="kanban_complete", args={"summary": FULL_PLAN, "result": "see summary"})
    assert verdict == {"action": "modify", "args": {"result": FULL_PLAN}}


def test_third_rework_round_must_block_instead(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE task_runs (task_id TEXT, outcome TEXT)")
    conn.executemany("INSERT INTO task_runs VALUES (?, ?)", [("t_1", "changes_requested")] * 2 + [("t_2", "changes_requested")])
    conn.commit(); conn.close()
    monkeypatch.setattr(submit, "_db", lambda: db)
    verdict = review_gate.gate(tool_name="kanban_request_changes", args={"task_id": "t_1", "reason": "x"})
    assert verdict["action"] == "block" and "kanban_block" in verdict["message"]
    assert review_gate.gate(tool_name="terminal", args={"command": "hermes kanban request-changes t_1 'fix'"})["action"] == "block"
    assert review_gate.gate(tool_name="kanban_request_changes", args={"task_id": "t_2", "reason": "x"}) is None


def test_worker_cannot_start_a_nested_agent(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    blocked = review_gate.gate("terminal", {"command": "hermes -p cypher chat -q 'judge this diff'"})
    assert blocked and blocked["action"] == "block"
    assert review_gate.gate("terminal", {"command": "git log --oneline | grep chat"}) is None
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    assert review_gate.gate("terminal", {"command": "hermes -p cypher chat -q hi"}) is None
