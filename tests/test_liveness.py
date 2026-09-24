from types import SimpleNamespace
import json
import sqlite3

import pytest

from software_delivery import liveness


@pytest.fixture(autouse=True)
def _reset_state():
    liveness._notice_seen.clear()
    liveness._count_cache.update(at=0.0, home=None, counts=None)
    yield
    liveness._notice_seen.clear()


def _result(**fields):
    base = dict(spawned=[], skipped_per_profile_capped=[], respawn_guarded=[],
                skipped_nonspawnable=[], skipped_unassigned=[], rate_limited=[],
                memory_pressure=None)
    base.update(fields)
    return SimpleNamespace(**base)


def _board(home, statuses):
    conn = sqlite3.connect(home / "kanban.db")
    conn.execute("CREATE TABLE tasks (id TEXT, status TEXT)")
    conn.executemany("INSERT INTO tasks VALUES (?, ?)", [(f"t{i}", s) for i, s in enumerate(statuses)])
    conn.commit()
    conn.close()


def test_tick_records_spawns_and_hold_reasons(tmp_path):
    liveness.record_dispatch_tick(
        outcome="ok", home=tmp_path, now=100.0,
        result=_result(spawned=[("t1", "forge", "/ws")],
                       skipped_per_profile_capped=[("t2", "forge", 3)],
                       respawn_guarded=[("t3", "active_pr")]))

    health = liveness.read_health(tmp_path)
    assert health["last_tick_at"] == 100.0 and health["last_spawn_count"] == 1
    assert health["holds"] == {
        "t2": {"reason": "per_profile_cap (forge running 3)", "since": 100.0},
        "t3": {"reason": "respawn_guarded:active_pr", "since": 100.0},
    }


def test_hold_since_survives_while_reason_is_unchanged(tmp_path):
    capped = _result(skipped_per_profile_capped=[("t2", "forge", 3)])
    liveness.record_dispatch_tick(outcome="ok", result=capped, home=tmp_path, now=100.0)
    liveness.record_dispatch_tick(outcome="ok", result=capped, home=tmp_path, now=200.0)
    assert liveness.read_health(tmp_path)["holds"]["t2"]["since"] == 100.0

    liveness.record_dispatch_tick(outcome="ok", result=_result(), home=tmp_path, now=300.0)
    assert liveness.read_health(tmp_path)["holds"] == {}


def test_locked_tick_only_refreshes_tick_time(tmp_path):
    liveness.record_dispatch_tick(outcome="ok", home=tmp_path, now=100.0,
                                  result=_result(skipped_unassigned=["t9"]))
    liveness.record_dispatch_tick(outcome="skipped_locked", result=None, home=tmp_path, now=150.0)
    health = liveness.read_health(tmp_path)
    assert health["last_tick_at"] == 150.0 and "t9" in health["holds"]


def test_dry_run_and_other_boards_are_ignored(tmp_path):
    liveness.record_dispatch_tick(outcome="ok", result=_result(), dry_run=True, home=tmp_path)
    liveness.record_dispatch_tick(outcome="ok", result=_result(), board="side", home=tmp_path)
    assert liveness.read_health(tmp_path) is None


def test_tick_never_raises(tmp_path):
    (tmp_path / "logs").write_text("not a directory")
    liveness.record_dispatch_tick(outcome="ok", result=_result(), home=tmp_path)


def test_estop_notice_injected_once_per_session(tmp_path):
    _board(tmp_path, ["ready", "review", "done"])
    (tmp_path / "ESTOP").write_text(json.dumps({"engaged_at": "2026-09-23T16:54:59+00:00"}))

    first = liveness.liveness_notice(session_id="s1", home=tmp_path)
    assert first and "ESTOP" in first["context"] and "hermes resume" in first["context"]
    assert "2 ready/review card(s)" in first["context"]
    assert "Do not work around it with `hermes kanban dispatch`" in first["context"]
    assert liveness.liveness_notice(session_id="s1", home=tmp_path) is None
    assert liveness.liveness_notice(session_id="s2", home=tmp_path) is not None


def test_estop_without_waiting_work_is_quiet(tmp_path):
    _board(tmp_path, ["done", "blocked"])
    (tmp_path / "ESTOP").write_text("{}")
    assert liveness.liveness_notice(session_id="s1", home=tmp_path) is None


def test_silent_dispatcher_notice(tmp_path):
    _board(tmp_path, ["ready"])
    liveness.record_dispatch_tick(outcome="idle", result=_result(), home=tmp_path, now=1000.0)

    assert liveness.liveness_notice(session_id="s1", home=tmp_path, now=1100.0) is None
    notice = liveness.liveness_notice(session_id="s1", home=tmp_path, now=1000.0 + 600)
    assert notice and "has not ticked for 10m" in notice["context"]


def test_doctor_report_lines(tmp_path):
    _board(tmp_path, ["ready", "blocked", "blocked"])
    (tmp_path / "ESTOP").write_text(json.dumps({"reason": "maintenance"}))
    liveness.record_dispatch_tick(outcome="ok", home=tmp_path, now=1000.0,
                                  result=_result(skipped_per_profile_capped=[("t0", "forge", 3)]))

    lines = liveness.liveness_report(home=tmp_path, now=1060.0)

    assert lines[0].startswith("estop: ENGAGED (reason: maintenance)")
    assert lines[1] == "dispatcher: last tick 60s ago (ok), last spawn never recorded"
    assert "dispatcher holds: per_profile_cap=1" in lines
    assert lines[-1] == "board: ready=1, review=0, running=0, todo=0, blocked=2, triage=0"
