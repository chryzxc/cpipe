"""Regression for issue: a ready card the engine dispatcher never promotes to running
stalls the Bot workflow silently.

The gateway's own dispatcher already detects this ("kanban dispatcher stuck: ready queue
non-empty ... 0 workers spawned") but only logs it to gateway.log, which nothing surfaces to
the board or a human. The stall scanner must detect the same condition independently from the
DB — tracking how long a card has actually been `ready` across ticks via the persisted
progress-state file, not row `created_at` (a card can sit `blocked` for days before becoming
ready) — and surface it as READY_STUCK so the supervisor cron agent (or a human) sees it and
re-dispatches.
"""
import json
import time

from tests.test_worker_readiness import add_card, git_workspace, run_scan, scanner


def _seed_progress_state(tmp_path, entries):
    path = tmp_path / "hermes" / "logs" / "supervisor-progress-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries))


def test_ready_card_stuck_past_threshold_is_flagged(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready", title="Stuck ready card")
    conn.commit()
    _seed_progress_state(tmp_path, {
        "ready1": {"status": "ready", "hb": 0, "ready_since": time.time() - 20 * 60},
    })

    output = run_scan(monkeypatch, capsys)

    assert "READY_STUCK · ready1 · ready 20m without the dispatcher promoting it to running" in output
    assert "hermes kanban dispatch" in output
    assert "Stuck ready card" in output
    conn.close()


def test_freshly_ready_card_not_yet_flagged(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready")
    conn.commit()
    # No prior progress-state entry: this is the first tick that observes it ready,
    # so ready_since is "now" — must not be flagged as stuck immediately.

    output = run_scan(monkeypatch, capsys)

    assert "READY_STUCK" not in output
    conn.close()


def test_estop_engaged_suppresses_ready_stuck_in_favor_of_paused(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready")
    conn.commit()
    _seed_progress_state(tmp_path, {
        "ready1": {"status": "ready", "hb": 0, "ready_since": time.time() - 20 * 60},
    })
    (tmp_path / "hermes" / "ESTOP").write_text(json.dumps({"reason": "maintenance"}))

    output = run_scan(monkeypatch, capsys)

    assert "READY_STUCK" not in output
    assert "PAUSED_BY_ESTOP · 1 card(s)" in output
    conn.close()


def test_workspace_invalid_ready_card_not_double_flagged(tmp_path, monkeypatch, capsys):
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "ready", workspace="/no/such/path/at/all")
    conn.commit()
    _seed_progress_state(tmp_path, {
        "ready1": {"status": "ready", "hb": 0, "ready_since": time.time() - 20 * 60},
    })

    output = run_scan(monkeypatch, capsys)

    assert "WORKSPACE_INVALID · ready1" in output
    assert "READY_STUCK" not in output
    conn.close()


def test_ready_stuck_clears_once_dispatched(tmp_path, monkeypatch, capsys):
    """Once the card moves off ready, READY_STUCK must not keep firing for it."""
    conn = scanner(tmp_path, monkeypatch)
    add_card(conn, "ready1", "running")
    conn.commit()
    _seed_progress_state(tmp_path, {
        "ready1": {"status": "ready", "hb": 0, "ready_since": time.time() - 20 * 60},
    })

    output = run_scan(monkeypatch, capsys)

    assert "READY_STUCK" not in output
    conn.close()
