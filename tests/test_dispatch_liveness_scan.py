"""Scan signals that keep the delivery chain moving without an operator asking.

Covers the gaps behind issues #10/#11: review-lane stalls, dispatcher holds from the
plugin's tick telemetry, verdicts parked in block reasons, reasonless blocks, DAG-aware
orphan detection, and cards silently parked in triage.
"""
import json
import time

from tests.test_worker_readiness import add_card, add_comment, run_scan, scanner


def _health(tmp_path, data):
    (tmp_path / "hermes" / "logs" / "dispatch-health.json").write_text(json.dumps(data))


def _event(conn, tid, kind, reason, created_at=None):
    conn.execute(
        "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
        (tid, kind, json.dumps({"reason": reason}), created_at or time.time()))


def test_fresh_review_card_uses_review_requested_event(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "rev1", "review")  # created 2h ago
    _event(conn, "rev1", "review_requested", "", created_at=time.time() - 60)
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "REVIEW_STALLED" not in output
    conn.close()


def test_review_lane_disabled_is_not_stalled(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "rev1", "review")
    conn.commit()
    (tmp_path / "hermes" / "config.yaml").write_text("kanban:\n  review_dispatch: false\n")

    output = run_scan(monkeypatch, capsys)

    assert "REVIEW_STALLED" not in output
    conn.close()


def test_estop_counts_review_cards_and_suppresses_lane_signals(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready")
    add_card(conn, "rev1", "review")
    conn.commit()
    _health(tmp_path, {"last_tick_at": 1_700_000_000.0})
    (tmp_path / "hermes" / "ESTOP").write_text(json.dumps({"engaged_at": "2026-09-23T16:54:59+00:00"}))

    output = run_scan(monkeypatch, capsys)

    assert "PAUSED_BY_ESTOP · 2 card(s)" in output
    assert "since 2026-09-23T16:54:59+00:00" in output
    assert "REVIEW_STALLED" not in output and "DISPATCHER_SILENT" not in output
    conn.close()


def test_verdict_in_block_reason_is_parked(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "rv", "blocked", title="Review PR 12")
    _event(conn, "rv", "blocked",
           "REQUEST_CHANGES F1 F2 — kanban_request_changes failed: active run was not claimed from review")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "VERDICT_PARKED · rv · REQUEST_CHANGES recorded in the block reason" in output
    assert "COORDINATOR_WAKE · rv" not in output
    conn.close()


def test_block_without_reason_is_flagged_once(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "b1", "blocked", title="Silent block")
    conn.commit()

    assert "BLOCKED_NO_REASON · b1 · blocked without a recorded reason or comment" in run_scan(monkeypatch, capsys)

    add_comment(conn, "b1", "supervisor: blocked_no_reason — owner, record why this is blocked")
    conn.commit()
    assert "BLOCKED_NO_REASON" not in run_scan(monkeypatch, capsys)
    conn.close()


def test_done_card_with_linked_child_is_not_orphaned(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    conn.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    for tid in ("parent", "lonely"):
        add_card(conn, tid, "done")
        conn.execute("UPDATE tasks SET completed_at=? WHERE id=?", (time.time() - 600, tid))
    add_card(conn, "child", "todo")
    conn.execute("INSERT INTO task_links VALUES ('parent', 'child')")
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert "ORPHANED_CHAIN · lonely" in output
    assert "ORPHANED_CHAIN · parent" not in output
    conn.close()


def test_orphan_listing_is_capped(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    for i in range(11):
        add_card(conn, f"d{i:02d}", "done")
        conn.execute("UPDATE tasks SET completed_at=? WHERE id=?", (time.time() - 600 - i, f"d{i:02d}"))
    conn.commit()

    output = run_scan(monkeypatch, capsys)

    assert output.count("ORPHANED_CHAIN · ") == 8
    assert "ORPHANED_CHAIN_MORE · 3 more orphaned card(s)" in output
    conn.close()


def test_scan_output_is_stable_across_ticks(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "tr1", "triage")
    add_card(conn, "b1", "blocked")
    conn.commit()
    _health(tmp_path, {"last_tick_at": 1_700_000_000.0})

    assert run_scan(monkeypatch, capsys) == run_scan(monkeypatch, capsys)
    conn.close()


def test_any_undecided_block_wakes_until_continuation(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "plain", "blocked", title="Fix chat recipients")
    _event(conn, "plain", "blocked", "jest force-exit diagnostic; stop rule hit", time.time() - 1800)
    conn.commit()
    assert "COORDINATOR_WAKE · plain" in run_scan(monkeypatch, capsys)

    add_comment(conn, "plain", "CONTINUATION: blocker ci-env (owner Christian)", created_at=time.time() - 1200)
    conn.commit()
    assert "COORDINATOR_WAKE · plain" not in run_scan(monkeypatch, capsys)
    conn.close()


