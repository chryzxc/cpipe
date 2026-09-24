"""Regressions for issue #4 and its recurrence #6: ready/assigned-todo tasks stall silently
when ESTOP pauses delivery.

The stall scanner must classify a `ready` task, or an aging assigned `todo` task, held by the
global emergency stop as an intentional pause (`PAUSED_BY_ESTOP`), not an unexplained dispatcher
failure (`QUEUE_AGING`) — deterministically, via the local sentinel file, with no LLM call
involved.
"""
import json
import time

from tests.test_worker_readiness import add_card, run_scan, scanner


def test_estop_engaged_classifies_ready_cards_as_paused(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready")
    conn.commit()
    (tmp_path / "hermes" / "ESTOP").write_text(
        json.dumps({"reason": "nightly maintenance", "engaged_at": "2026-09-24T00:00:00Z"}))

    output = run_scan(monkeypatch, capsys)

    assert "PAUSED_BY_ESTOP · 1 card(s)" in output
    assert "nightly maintenance" in output
    assert "not a dispatcher failure" in output
    conn.close()


def test_no_estop_sentinel_stays_silent(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "PAUSED_BY_ESTOP" not in output
    conn.close()


def test_estop_engaged_without_ready_cards_stays_silent(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "todo1", "todo", assignee="")
    conn.commit()
    (tmp_path / "hermes" / "ESTOP").write_text(json.dumps({"reason": "paused"}))

    output = run_scan(monkeypatch, capsys)

    assert "PAUSED_BY_ESTOP" not in output
    conn.close()


def test_corrupt_estop_sentinel_still_reports_paused_fail_safe(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready")
    conn.commit()
    (tmp_path / "hermes" / "ESTOP").write_text("")  # empty/corrupt body — still engaged

    output = run_scan(monkeypatch, capsys)

    assert "PAUSED_BY_ESTOP · 1 card(s)" in output
    conn.close()


def _aging_todo_card(conn, tid):
    """A todo card assigned long enough ago to normally trip QUEUE_AGING (>=30m)."""
    conn.execute(
        "INSERT INTO tasks (id, title, status, assignee, skills, body, created_at)"
        " VALUES (?, 'Aging card', 'todo', 'forge', '[]', 'BUDGET: token_budget 20m', ?)",
        (tid, time.time() - 3600))


def test_estop_engaged_reclassifies_aging_assigned_todo_instead_of_queue_aging(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    _aging_todo_card(conn, "todo1")
    conn.commit()
    (tmp_path / "hermes" / "ESTOP").write_text(json.dumps({"reason": "paused for review"}))

    output = run_scan(monkeypatch, capsys)

    assert "QUEUE_AGING" not in output
    assert "PAUSED_BY_ESTOP · 1 card(s)" in output
    assert "paused for review" in output
    conn.close()


def test_no_estop_aging_assigned_todo_still_reports_queue_aging(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    _aging_todo_card(conn, "todo1")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "QUEUE_AGING · todo1 · assigned to forge but unstarted" in output
    assert "PAUSED_BY_ESTOP" not in output
    conn.close()


def test_estop_engaged_but_fresh_todo_not_yet_aging_stays_silent(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "fresh1", "todo", assignee="forge")
    conn.execute("UPDATE tasks SET created_at=? WHERE id='fresh1'", (time.time(),))
    conn.commit()
    (tmp_path / "hermes" / "ESTOP").write_text(json.dumps({"reason": "paused"}))

    output = run_scan(monkeypatch, capsys)

    assert "QUEUE_AGING" not in output
    assert "PAUSED_BY_ESTOP" not in output
    conn.close()
