"""Delivery-role cards come only from the delivery tools; other cards and the user's terminal are untouched."""
from cpipe import card_gate, submit


def setup(tmp_path, monkeypatch):
    (tmp_path / "roster.yaml").write_text("roles:\n  coordinator: default\n  implementer: forge\n"
                                          "  verifier: sentinel\n  security_reviewer: cypher\n  administrator: steward\n")
    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path)


def test_hand_made_delivery_cards_are_refused(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    for name, args in (("kanban_create", {"title": "Fix SEC-ME-001", "assignee": "forge"}),
                       ("kanban_create", {"title": "Security gate: month-end", "assignee": "cypher"}),
                       ("terminal", {"command": "hermes kanban create 'QA gate' --assignee sentinel --body x"})):
        verdict = card_gate.gate(tool_name=name, args=args)
        assert verdict["action"] == "block" and "delivery_submit" in verdict["message"]


def test_other_cards_and_tools_pass(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    assert card_gate.gate(tool_name="kanban_create", args={"title": "Standards draft", "assignee": "default"}) is None
    assert card_gate.gate(tool_name="terminal", args={"command": "hermes kanban create x --assignee steward"}) is None
    assert card_gate.gate(tool_name="terminal", args={"command": "hermes kanban list"}) is None
    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path / "missing")
    assert card_gate.gate(tool_name="kanban_create", args={"assignee": "forge"}) is None


def test_chat_cannot_unblock_a_card_at_the_round_limit(monkeypatch):
    from cpipe import card_gate
    reasons = {"t_aa11": "delivery_verify_failed hit round limit(2). failures", "t_bb22": "needs a decision"}
    monkeypatch.setattr(card_gate, "last_block_reason", lambda card: reasons.get(card, ""))
    assert card_gate.gate("kanban_unblock", {"task_id": "t_aa11"})["action"] == "block"
    assert card_gate.gate("terminal", {"command": "hermes kanban unblock t_aa11"})["action"] == "block"
    assert card_gate.gate("kanban_unblock", {"task_id": "t_bb22"}) is None


def test_workers_cannot_edit_the_operator_setup(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "forge"))
    blocked = [("patch", {"mode": "patch", "patch": f"*** Begin Patch\n*** Update File: {tmp_path}/delivery/projects.yaml\n@@"}),
               ("write_file", {"path": str(tmp_path / "config.yaml")}),
               ("patch", {"mode": "replace", "path": str(tmp_path / "profiles/sentry/SOUL.md")})]
    for name, args in blocked:
        assert card_gate.gate(name, args)["action"] == "block"
    for path in (tmp_path / "kanban/workspaces/t_1/a.py", tmp_path / "profiles/forge/notes.md", "/repo/src/a.js"):
        assert card_gate.gate("write_file", {"path": str(path)}) is None
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))  # the coordinator / the user's own chat
    assert card_gate.gate("write_file", {"path": str(tmp_path / "config.yaml")}) is None


def test_planner_plans_from_the_map_within_its_lookup_budget(tmp_path, monkeypatch):
    (tmp_path / "roster.yaml").write_text("roles:\n  planner: archon\n  implementer: forge\n")
    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(card_gate, "_lookups", {})
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "archon"))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_plan")
    titles = {"t_plan": "Plan: add reminders", "t_spec": "Decide OTP architecture"}
    monkeypatch.setattr(submit, "_task_row", lambda tid: {"title": titles[tid]})
    for _ in range(submit.PLAN_LOOKUPS):
        assert card_gate.gate("read_file", {"path": "/repo/a.js"}) is None
    assert card_gate.gate("terminal", {"command": "hermes kanban complete t_plan"}) is None
    assert card_gate.gate("terminal", {"command": "grep -rn x ."})["action"] == "block"
    assert card_gate.gate("kanban_complete", {"result": "plan"}) is None
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_spec")  # Archon's own non-delivery specs may explore
    assert all(card_gate.gate("read_file", {"path": "/a"}) is None for _ in range(10))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "forge"))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_plan")
    assert card_gate.gate("read_file", {"path": "/a"}) is None
