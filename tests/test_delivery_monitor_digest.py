"""The operator must hear when delivery stops moving, once, and hear nothing while it moves."""
import runpy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kanban_board import board, card, event  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "delivery_monitor.py"


def digest(conn, state, tmp_path):
    mod = runpy.run_path(str(SCRIPT))
    now = time.time()
    b = mod["load_board"](conn, now, tmp_path)
    verdicts = mod["classify_board"](b, now)
    current = {**mod["engine_stalls"](conn, now), **mod["card_stalls"](verdicts, b, now)}
    return mod["build_digest"](current, state, now)


def test_new_stall_alerts_once_then_resolution_is_reported(tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_wait", "blocked", minutes_ago=45, block_kind="needs_input")
    event(conn, "t_wait", "blocked", 45, reason="Pick option A or B")
    card(conn, "t_fresh", "blocked", minutes_ago=5, block_kind="needs_input")   # inside grace window
    card(conn, "t_busy", "running", minutes_ago=2, worker_pid=0)                # claim still starting

    message, state = digest(conn, {}, tmp_path)
    assert "t_wait" in message and "UNSUBSCRIBED" in message
    assert "Pick option A or B" in message
    assert "t_fresh" in message  # unsubscribed: nobody else will tell anyone
    assert "t_busy" not in message

    assert digest(conn, state, tmp_path)[0] == ""                              # no repeat while unchanged

    conn.execute("UPDATE tasks SET status='done' WHERE id='t_wait'")
    conn.commit()
    message, state = digest(conn, state, tmp_path)
    assert message == "" and "t_wait" not in str(state)  # a recovery alone is silent (no coordinator turn)


def test_engine_crash_storm_is_one_board_level_alert(tmp_path):
    conn = board(tmp_path / "kanban.db")
    for _ in range(3):
        event(conn, "t_x", "crashed", worker_output="No module hermes_cli.main")
    message, _ = digest(conn, {}, tmp_path)
    assert "Workers are crashing on start" in message and "hermes_cli.main" in message


def test_dead_worker_and_unclaimed_ready_card_are_stalls(tmp_path):
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_ready", "ready", minutes_ago=30)
    card(conn, "t_dead", "running", minutes_ago=60, worker_pid=2 ** 22 + 7, last_heartbeat_at=int(time.time()) - 3600)
    message, _ = digest(conn, {}, tmp_path)
    assert "t_ready" in message and "UNKNOWN_STALL" in message
    assert "t_dead" in message and "DEAD_WORKER" in message


def test_digest_fits_a_chat_message(tmp_path):
    conn = board(tmp_path / "kanban.db")
    for i in range(60):
        card(conn, f"t_{i}", "blocked", minutes_ago=60 + i, block_kind="capability")
        event(conn, f"t_{i}", "blocked", 60 + i, reason="x" * 300)
    message, _ = digest(conn, {}, tmp_path)
    assert len(message) < 2000 and "more —" in message
