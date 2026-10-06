"""A chat turn that grinds through tool calls is nudged to hand the work to a worker; workers are never capped."""
from cpipe import status


def test_chat_turns_are_nudged_every_budget_and_workers_never(monkeypatch):
    monkeypatch.setattr(status, "_turn_calls", {})
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    calls = [status.turn_budget("terminal", session_id="s", turn_id="u1") for _ in range(status.TURN_CALLS + 1)]
    assert calls[:-1] == [None] * status.TURN_CALLS and "delivery_submit" in calls[-1]["message"]
    assert status.turn_budget("terminal", session_id="s", turn_id="u1") is None  # repeating carries on
    assert status.turn_budget("terminal", session_id="s", turn_id="u2") is None  # a new turn starts over
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    monkeypatch.setattr(status, "_turn_calls", {})
    assert all(status.turn_budget("terminal", session_id="s", turn_id="u") is None for _ in range(200))
