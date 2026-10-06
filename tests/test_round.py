"""The graph's edges, the codex command, and one delivery end to end: scheduler → round adapter → cpipe.round →
fake codex, on a fixture repo and a temp board."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpipe import graph, settings
from cpipe.engines import codex

from conftest import make_task, wait_for
from fixtures.make_repo import TASK, install, make_repo

FAKE = str(Path(__file__).with_name("fake_codex.py"))
LIMITS = settings.DEFAULTS["limits"]


@pytest.mark.parametrize("node,outcome,rounds,want", [
    ("setup", "ok", {}, "build"),
    ("build", "ok", {}, "checks"),
    ("build", "needs_user", {}, "ask"),
    ("build", "rate_limited", {}, "rate_limited"),
    ("build", "failed", {}, "failed"),
    ("checks", "ok", {}, "handoff"),
    ("checks", "fail", {"checks": 1}, "build"),
    ("checks", "fail", {"checks": 2}, "build"),
    ("checks", "fail", {"checks": 3}, "stuck"),
])
def test_edges(node, outcome, rounds, want):
    assert graph.next_node(node, outcome, rounds, LIMITS) == want


def test_codex_command(tmp_path):
    fx = make_repo(tmp_path, "python")
    o, s = tmp_path / "o.json", tmp_path / "s.json"
    start = codex.command({"bin": "cx", "model": "m"}, "do it", fx.repo, None, s, o)
    assert start[:2] == ["cx", "exec"] and start[-3:] == ["-C", str(fx.repo), "do it"]
    assert ["-m", "m"] == start[start.index("-m"):start.index("-m") + 2]
    assert f'sandbox_workspace_write.writable_roots=["{(fx.repo / ".git").resolve()}"]' in start
    resume = codex.command({"bin": "cx"}, "more", fx.repo, "T1", s, o)
    assert resume[:4] == ["cx", "exec", "resume", "T1"] and "-C" not in resume and resume[-1] == "more"


def scheduler():
    """Lanes from a cpipe.yaml in the temp HERMES_HOME: the round reads the same file, so it runs the fake."""
    from cpipe.runtime.config import parse_config
    from cpipe.runtime.scheduler import Scheduler
    settings.path().write_text(json.dumps({"engines": {"codex": {"bin": FAKE, "concurrency": 1}},
                                           "env_pass": ["FAKE_CODEX_LOG", "FAKE_CODEX"]}))
    cfg = settings.load()
    raw = settings.runtime_raw(cfg)
    return Scheduler(parse_config({**raw, "heartbeat_interval_seconds": 0.5,
                                   "worker_heartbeat_interval_seconds": 1, "poll_interval_seconds": 0.2}))


def run_until(board, sched, tid, want):
    from cpipe.runtime import hermes_api as h
    sched.tick()
    return wait_for(lambda: h.get_task(board, tid).status not in ("ready", "running")
                    and h.get_task(board, tid).status, 60) == want


def test_delivery_round_trip(board, hermes_home, tmp_path, monkeypatch):
    from cpipe.runtime import hermes_api as h
    fx = make_repo(tmp_path, "python")
    install([fx], hermes_home)
    fake_log = tmp_path / "codex.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(fake_log))
    sched = scheduler()
    tid = make_task(board, assignee="cp:codex", title=TASK, body="", workspace_kind="worktree",
                    workspace_path=str(fx.repo), branch_name="cp/fix-add")

    assert run_until(board, sched, tid, "review"), h.worker_log_path(tid, board=None).read_text()
    calls = [json.loads(ln) for ln in fake_log.read_text().splitlines()]
    assert [c["resumed"] for c in calls] == [False, True]          # wrong fix, then the check failure fed back
    assert "checks failed" in calls[1]["prompt"] and calls[1]["thread"] == calls[0]["thread"]
    ws = Path(h.get_task(board, tid).workspace_path)
    state = json.loads((ws / ".cpipe" / "state.json").read_text())
    assert state["project"] == fx.project and state["session"] == calls[0]["thread"]
    assert state["usage"]["input_tokens"] == 2000 and "a + b" in (ws / "calc.py").read_text()

    h.comment(board, tid, "also leave a note in calc.py", author="user")
    h.kb.reopen_review_task(board, tid)
    assert run_until(board, sched, tid, "review"), h.worker_log_path(tid, board=None).read_text()
    last = json.loads(fake_log.read_text().splitlines()[-1])
    assert last["resumed"] and last["thread"] == calls[0]["thread"] and "also leave a note" in last["prompt"]
    assert "feedback addressed" in (ws / "calc.py").read_text()


def test_rate_limit_requeues(board, hermes_home, tmp_path, monkeypatch):
    from cpipe.runtime import hermes_api as h
    fx = make_repo(tmp_path, "python")
    install([fx], hermes_home)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "codex.jsonl"))
    monkeypatch.setenv("FAKE_CODEX", "rate_limit")
    sched = scheduler()
    tid = make_task(board, assignee="cp:codex", title=TASK, body="", workspace_kind="worktree",
                    workspace_path=str(fx.repo), branch_name="cp/fix-add")
    sched.tick()
    log = h.worker_log_path(tid, board=None)
    wait_for(lambda: "[kanban-worker-exit] rc=" in log.read_text(), 60)
    # Hermes' crash sweep books this exit code as a quota requeue (not a failure)
    assert f"[kanban-worker-exit] rc={h.EXIT_RATE_LIMITED}" in log.read_text()
    assert "[rate_limited]" in log.read_text()
