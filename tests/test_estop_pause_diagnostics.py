"""Regression for issue #4: ready tasks stall silently when ESTOP pauses delivery.

The stall scanner must classify a `ready` task held by the global emergency stop as an
intentional pause (`PAUSED_BY_ESTOP`), not an unexplained dispatcher failure — deterministically,
via the local sentinel file, with no LLM call involved.
"""
import json

from tests.test_worker_readiness import add_card, run_scan, scanner


def test_estop_engaged_classifies_ready_cards_as_paused(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready")
    conn.commit()
    (tmp_path / "hermes" / "ESTOP").write_text(
        json.dumps({"reason": "nightly maintenance", "engaged_at": "2026-09-24T00:00:00Z"}))

    output = run_scan(monkeypatch, capsys)

    assert "PAUSED_BY_ESTOP · 1 ready card(s)" in output
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

    assert "PAUSED_BY_ESTOP · 1 ready card(s)" in output
    conn.close()
