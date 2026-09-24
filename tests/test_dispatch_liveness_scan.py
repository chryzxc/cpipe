"""Scan signals that keep the delivery chain moving without an operator asking.

Covers the gaps behind issues #10/#11: review-lane stalls, dispatcher holds from the
plugin's tick telemetry, verdicts parked in block reasons, reasonless blocks, DAG-aware
orphan detection, and cards silently parked in triage.
"""
import json
import time

from tests.test_worker_readiness import add_card, add_comment, run_scan, scanner


def _progress(tmp_path, entries):
    path = tmp_path / "hermes" / "logs" / "supervisor-progress-state.json"
    path.write_text(json.dumps(entries))


def _health(tmp_path, data):
    (tmp_path / "hermes" / "logs" / "dispatch-health.json").write_text(json.dumps(data))


def _event(conn, tid, kind, reason, created_at=None):
    conn.execute(
        "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
        (tid, kind, json.dumps({"reason": reason}), created_at or time.time()))


def test_review_card_waiting_past_threshold_is_stalled(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "rev1", "review", title="Review auth change")
    conn.commit()
    _progress(tmp_path, {"rev1": {"status": "review", "hb": 0, "review_since": time.time() - 20 * 60}})

    output = run_scan(monkeypatch, capsys)

    assert "REVIEW_STALLED · rev1 · review requested 0h ago, no reviewer dispatched" in output
    conn.close()


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


def test_per_profile_cap_hold_is_queued_not_stuck(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready", title="Queued card")
    conn.commit()
    now = time.time()
    _progress(tmp_path, {"ready1": {"status": "ready", "hb": 0, "ready_since": now - 30 * 60}})
    _health(tmp_path, {"last_tick_at": now - 10, "holds": {
        "ready1": {"reason": "per_profile_cap (sentry running 3)", "since": now - 30 * 60}}})

    output = run_scan(monkeypatch, capsys)

    assert "READY_STUCK" not in output
    assert "QUEUED_AT_CAP · ready1 · ready card queued behind per_profile_cap (sentry running 3)" in output
    assert "DISPATCHER_SILENT" not in output
    conn.close()


def test_other_engine_hold_annotates_ready_stuck(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready")
    conn.commit()
    now = time.time()
    _progress(tmp_path, {"ready1": {"status": "ready", "hb": 0, "ready_since": now - 30 * 60}})
    _health(tmp_path, {"last_tick_at": now - 10, "holds": {
        "ready1": {"reason": "respawn_guarded:blocker_auth", "since": now}}})

    output = run_scan(monkeypatch, capsys)

    assert "READY_STUCK · ready1" in output
    assert "engine hold: respawn_guarded:blocker_auth" in output
    conn.close()


def test_silent_dispatcher_with_waiting_work(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready")
    conn.commit()
    _health(tmp_path, {"last_tick_at": 1_700_000_000.0, "holds": {}})

    output = run_scan(monkeypatch, capsys)

    assert ("DISPATCHER_SILENT · gateway dispatcher last ticked at 2023-11-14T22:13:20Z "
            "while 1 ready/review card(s) wait") in output
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


def test_triage_card_surfaces_block_loop_reason(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "tr1", "triage", title="Looping card")
    _event(conn, "tr1", "block_loop_detected", "same dependency block raised 3 times")
    conn.commit()

    output = run_scan(monkeypatch, capsys)
    assert ("TRIAGE_PARKED · tr1 · parked in triage, nothing dispatches it · "
            "same dependency block raised 3 times · Looping card") in output

    add_comment(conn, "tr1", "coordinator: triage_parked — waiting on operator")
    conn.commit()
    assert "TRIAGE_PARKED" not in run_scan(monkeypatch, capsys)
    conn.close()


def test_scan_output_is_stable_across_ticks(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "tr1", "triage")
    add_card(conn, "b1", "blocked")
    conn.commit()
    _health(tmp_path, {"last_tick_at": 1_700_000_000.0})

    assert run_scan(monkeypatch, capsys) == run_scan(monkeypatch, capsys)
    conn.close()
