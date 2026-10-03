"""Replays of real incident shapes on the real kanban schema: the monitor must name each one
correctly, repair only what it can prove, and never leave a chain with no owner."""
import json
import runpy
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kanban_board import board, card, event, link, run, subscribe  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "delivery_monitor.py"
QUOTA_ERR = "pid 4242 exited rate-limited (quota wall) — requeued without counting a failure"


@pytest.fixture
def mon(tmp_path):
    return runpy.run_path(str(SCRIPT))


def tick(mon, conn, tmp_path, st=None, watches=None, mode="act", calls=None, gh=None):
    calls = [] if calls is None else calls
    st = {} if st is None else st

    def fake_run(*args):
        calls.append(args)
        return True, "ok"

    verdicts, b, notices = mon["tick"](conn, time.time(), st, watches if watches is not None else {}, mode=mode,
                                       run=fake_run, gh=gh or (lambda *a: (False, "")),
                                       db=tmp_path / "kanban.db", home=tmp_path)
    return verdicts, notices, calls, st


def journal(tmp_path):
    path = tmp_path / "logs" / "delivery-journal.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def test_stale_quota_guard_on_a_review_card_is_cleared(mon, tmp_path):
    """A review card held by blocker_auth on an old quota requeue, while its latest run succeeded."""
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_rev", "review", minutes_ago=40, last_failure_error=QUOTA_ERR)
    run(conn, "t_rev", "rate_limited", started_ago=90, seconds=30, error=QUOTA_ERR)
    run(conn, "t_rev", "review_requested", started_ago=60)
    event(conn, "t_rev", "respawn_guarded", 1, reason="blocker_auth")

    verdicts, _, _, st = tick(mon, conn, tmp_path)
    assert verdicts["t_rev"].cause == "STALE_GUARD"
    assert conn.execute("SELECT last_failure_error FROM tasks WHERE id='t_rev'").fetchone()[0] is None
    assert any(e["kind"] == "monitor.repair" and e["repair"] == "clear_guard" and e["ok"] for e in journal(tmp_path))


def test_observe_mode_only_journals(mon, tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_rev", "review", minutes_ago=40, last_failure_error=QUOTA_ERR)
    run(conn, "t_rev", "review_requested", started_ago=60)
    event(conn, "t_rev", "respawn_guarded", 1, reason="blocker_auth")
    _, _, calls, _ = tick(mon, conn, tmp_path, mode="observe")
    assert conn.execute("SELECT last_failure_error FROM tasks WHERE id='t_rev'").fetchone()[0] == QUOTA_ERR
    assert calls == [] and any(e["kind"] == "monitor.would_repair" for e in journal(tmp_path))


def test_real_auth_failure_escalates_instead_of_clearing(mon, tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_auth", "ready", minutes_ago=40, last_failure_error="401 unauthorized: invalid api key")
    run(conn, "t_auth", "crashed", started_ago=50, error="401 unauthorized: invalid api key")
    event(conn, "t_auth", "respawn_guarded", 1, reason="blocker_auth")
    verdicts, _, calls, _ = tick(mon, conn, tmp_path)
    assert verdicts["t_auth"].cause == "AUTH_BLOCKED"
    assert calls[0][:4] == ("kanban", "block", "t_auth", "--kind")
    assert "AUTH_BLOCKED" in calls[0][-1] and "next:" in calls[0][-1]


def test_identical_failure_after_retry_escalates_once(mon, tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_loop", "ready", minutes_ago=30)
    run(conn, "t_loop", "blocked", started_ago=120, error="npm ERR! missing script: build (pid 11)")
    event(conn, "t_loop", "unblocked", 100)
    run(conn, "t_loop", "blocked", started_ago=60, error="npm ERR! missing script: build (pid 98)")
    verdicts, _, calls, st = tick(mon, conn, tmp_path)
    assert verdicts["t_loop"].cause == "IDENTICAL_FAILURE"
    assert len([c for c in calls if c[1] == "block"]) == 1
    _, _, calls2, _ = tick(mon, conn, tmp_path, st=st)
    assert not [c for c in calls2 if c[1] == "block"]  # deduped per (card, cause, signature)
    assert any(e["kind"] == "waste.identical_failure" for e in journal(tmp_path))


def test_quota_wall_pauses_ready_cards_and_waits_on_review(mon, tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_r", "ready", minutes_ago=25, assignee="critic")
    card(conn, "t_v", "review", minutes_ago=25, assignee="critic")
    for _ in range(3):
        event(conn, "t_r", "rate_limited", 5, retry_status="requeued")
    verdicts, _, calls, st = tick(mon, conn, tmp_path)
    assert verdicts["t_r"].label.startswith("WAITING(critic quota")
    assert verdicts["t_v"].state == "WAITING"
    assert ("-p", "critic", "usage", "--json") == calls[0] and ("kanban", "schedule") == calls[1][:2]
    assert 25 * 60 < st["quota_pause"]["t_r"] - time.time() <= 30 * 60  # usage said nothing: fixed pause
    st["quota_pause"]["t_r"] = time.time() - 1
    conn.execute("UPDATE tasks SET status='scheduled' WHERE id='t_r'")
    conn.commit()
    _, _, calls, _ = tick(mon, conn, tmp_path, st=st)
    assert ("kanban", "unblock", "t_r") == calls[0][:3]


def test_quota_pause_waits_for_the_providers_reset(mon):
    from datetime import datetime, timedelta, timezone
    now = time.time()
    week = datetime.now(timezone.utc) + timedelta(days=2)
    usage = {"windows": [{"used_percent": 40, "resets_at": (week - timedelta(days=1)).isoformat()},
                         {"used_percent": 100, "resets_at": week.isoformat()}]}
    assert mon["quota_reset"]("critic", now, lambda *a: (True, json.dumps(usage))) == week.timestamp() + 120
    assert mon["quota_reset"]("critic", now, lambda *a: (False, "")) == now + 30 * 60


def test_parked_triage_after_rework_is_waiting_when_subscribed(mon, tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_tri", "triage", minutes_ago=90)
    subscribe(conn, "t_tri")
    verdicts, notices, calls, _ = tick(mon, conn, tmp_path)
    assert verdicts["t_tri"].label == "WAITING(human: triage decision)"
    assert calls == [] and notices == []


def test_unsubscribed_child_inherits_the_chain_chat(mon, tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_root", "done", minutes_ago=90)
    card(conn, "t_kid", "running", minutes_ago=5, worker_pid=0)
    link(conn, "t_root", "t_kid")
    subscribe(conn, "t_root", "chat-9")
    _, _, calls, _ = tick(mon, conn, tmp_path)
    assert ("kanban", "notify-subscribe", "t_kid", "--platform", "discord", "--chat-id", "chat-9") == calls[0][:7]


def test_review_card_escalation_reaches_the_chat_as_a_notice(mon, tmp_path):
    """Review cards cannot be blocked; the origin chat gets a notice on its next turn instead."""
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_orphan", "review", minutes_ago=45)
    subscribe(conn, "t_orphan", "chat-5")
    verdicts, notices, calls, _ = tick(mon, conn, tmp_path)
    assert verdicts["t_orphan"].cause == "ORPHAN_REVIEW"
    assert not [c for c in calls if c[1] == "block"]
    assert notices and notices[0]["chat_id"] == "chat-5" and "ORPHAN_REVIEW" in notices[0]["text"]


def test_watched_ci_failure_escalates(mon, tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_pr", "blocked", minutes_ago=30, block_kind="dependency")
    subscribe(conn, "t_pr")
    watches = {"t_pr": {"kind": "ci", "ref": "https://github.com/o/r/pull/1", "until": time.time() + 3600}}
    gh = lambda *a: (True, json.dumps({"state": "OPEN", "statusCheckRollup": [{"conclusion": "FAILURE"}]}))  # noqa: E731
    _, notices, _, _ = tick(mon, conn, tmp_path, watches=watches, gh=gh)
    assert watches == {} and "WATCH_FAILED" in notices[0]["text"]


def test_expired_decision_is_archived_with_a_final_comment(mon, tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_old", "blocked", minutes_ago=11 * 1440, block_kind="needs_input")
    subscribe(conn, "t_old")
    verdicts, notices, calls, st = tick(mon, conn, tmp_path)
    assert verdicts["t_old"].cause == "EXPIRING" and "archived" in notices[0]["text"]  # warned first, never archived cold
    assert not [c for c in calls if c[1] == "archive"]
    st["expiry_warned"]["t_old"] -= 4 * 86400
    _, _, calls, _ = tick(mon, conn, tmp_path, st=st)
    assert calls[0][:3] == ("kanban", "comment", "t_old") and "FINAL:" in calls[0][3]
    assert calls[1] == ("kanban", "archive", "t_old")


def test_every_open_card_has_exactly_one_named_verdict(mon, tmp_path):
    """The chain invariant: every open card is PROGRESSING, WAITING on something named, or STUCK
    with a cause; a stuck parent owns its children's stall instead of each child alerting."""
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_p", "ready", minutes_ago=60)
    for kid in ("t_c1", "t_c2"):
        card(conn, kid, "todo", minutes_ago=60)
        link(conn, "t_p", kid)
    card(conn, "t_s", "scheduled", minutes_ago=5)
    card(conn, "t_b", "blocked", minutes_ago=30, block_kind="needs_input")
    verdicts, _, _, _ = tick(mon, conn, tmp_path, mode="observe")
    open_ids = {r[0] for r in conn.execute("SELECT id FROM tasks WHERE status NOT IN ('done','archived')")}
    assert set(verdicts) == open_ids
    for v in verdicts.values():
        assert v.state in ("PROGRESSING", "WAITING", "STUCK") and v.cause
        if v.state == "WAITING":
            assert v.waiting_on
    assert verdicts["t_p"].state == "STUCK" and sorted(verdicts["t_p"].blocked_children) == ["t_c1", "t_c2"]
    assert verdicts["t_c1"].cause == "PARENT_STUCK"


def test_signature_ignores_pids_paths_and_numbers(mon):
    sig = mon["signature"]
    assert sig("pid 12 failed at /tmp/a/b.py line 4") == sig("pid 99 failed at /var/x.py line 70")
    assert sig("missing script: build") != sig("missing script: test")
