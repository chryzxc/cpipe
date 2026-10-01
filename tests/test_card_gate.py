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
