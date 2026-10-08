"""Existing-card actions must report scheduling, not daemon readiness."""
from types import SimpleNamespace

import pytest
from cpipe import tools


def nullcontext():
    return SimpleNamespace(close=lambda: None)


@pytest.mark.parametrize("action", [tools.build, tools.ship, tools.feedback])
def test_triage_is_not_reported_as_resumed(monkeypatch, action):
    monkeypatch.setattr("cpipe.report.state_of", lambda *a: {})
    task = SimpleNamespace(id="t_example", status="triage", created_by="cpipe")
    monkeypatch.setattr(tools.hermes_api, "connect", lambda *a: nullcontext())
    monkeypatch.setattr(tools.hermes_api, "get_task", lambda *a: task)
    monkeypatch.setattr(tools, "_activate", lambda *a: None)
    monkeypatch.setattr(tools.hermes_api, "comment", lambda *a, **k: None)
    monkeypatch.setattr(tools.hermes_api, "resume", lambda *a: False)
    result = action({"task_id": task.id, "text": "resume after repair"})
    assert "error" in result
    assert result["status"] == "triage"
    assert result["dispatch_scheduled"] is False


def test_failed_resume_reports_actual_state(monkeypatch):
    task = SimpleNamespace(id="t_example", status="blocked", created_by="cpipe")
    monkeypatch.setattr(tools.hermes_api, "connect", lambda *a: nullcontext())
    monkeypatch.setattr(tools.hermes_api, "get_task", lambda *a: task)
    monkeypatch.setattr(tools, "_activate", lambda *a: None)
    monkeypatch.setattr(tools.hermes_api, "comment", lambda *a, **k: None)
    monkeypatch.setattr(tools.hermes_api, "resume", lambda *a: False)
    result = tools.build({"task_id": task.id})
    assert "error" in result
    assert result["dispatch_scheduled"] is False


def test_ready_without_claim_is_queued_not_dispatched(monkeypatch):
    task = SimpleNamespace(id="t_example", status="ready", created_by="cpipe")
    monkeypatch.setattr(tools.hermes_api, "connect", lambda *a: nullcontext())
    monkeypatch.setattr(tools.hermes_api, "get_task", lambda *a: task)
    monkeypatch.setattr(tools, "_activate", lambda *a: None)
    monkeypatch.setattr(tools.hermes_api, "comment", lambda *a, **k: None)
    monkeypatch.setattr(tools.hermes_api, "resume", lambda *a: False)
    result = tools.build({"task_id": task.id})
    assert result["status"] == "ready"
    assert result["dispatch_scheduled"] is True
    assert result["dispatched"] is False
