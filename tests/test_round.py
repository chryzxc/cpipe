"""The graph's edges and loops, the engine commands, and deliveries end to end: chat tool → scheduler → round
adapter → cpipe.round → fake codex and fake gh, on a fixture repo and a temp board."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cpipe import engines, graph, settings
from cpipe.engines import claude, codex, hermes, opencode

from conftest import wait_for
from fixtures.make_repo import TASK, install, make_repo

HERE = Path(__file__).parent
FAKE = str(HERE / "fake_codex.py")
LIMITS = settings.DEFAULTS["limits"]


@pytest.mark.parametrize("node,outcome,ready,want", [
    ("setup", "ok", False, "build"),
    ("setup", "large", False, "plan"),
    ("plan", "split", False, "split"),
    ("plan", "ok", False, "build"),
    ("split", "ok", False, "done"),
    ("build", "ok", False, "checks"),
    ("build", "needs_user", False, "ask"),
    ("build", "rate_limited", False, "rate_limited"),
    ("checks", "ok", False, "verify"),
    ("checks", "fix", False, "build"),
    ("checks", "limit", False, "stuck"),
    ("verify", "ok", False, "pr"),
    ("pr", "blocked", False, "stuck"),
    ("ci", "ok", False, "handoff"),
    ("ci", "ok", True, "final"),
    ("ci", "fix", True, "build"),
    ("final", "ok", True, "done"),
])
def test_edges(node, outcome, ready, want):
    assert graph.next_node(node, outcome, ready) == want


def test_no_edge():
    with pytest.raises(ValueError):
        graph.next_node("build", "fix")


def test_loop_outcome():
    rounds = {}
    assert [graph.loop_outcome(["checks"], rounds, LIMITS) for _ in range(4)] == ["fix", "fix", "fix", "limit"]
    rounds = {}
    assert graph.loop_outcome([], rounds, LIMITS) == "ok" and rounds == {}
    # review is soft: out of budget it passes, its findings go into the PR
    rounds = {"review": 2}
    assert graph.loop_outcome(["review"], rounds, LIMITS) == "ok"
    assert graph.loop_outcome(["review", "qa"], {"review": 2}, LIMITS) == "fix"
    assert graph.loop_outcome(["review", "qa"], {"review": 2, "qa": 2}, LIMITS) == "limit"


def test_codex_command(tmp_path):
    fx = make_repo(tmp_path, "python")
    o, s = tmp_path / "o.json", tmp_path / "s.json"
    start = codex.command({"bin": "cx", "model": "m"}, "do it", fx.repo, None, s, o)
    assert start[:2] == ["cx", "exec"] and start[-3:] == ["-C", str(fx.repo), "do it"]
    assert ["-m", "m"] == start[start.index("-m"):start.index("-m") + 2]
    assert f'sandbox_workspace_write.writable_roots=["{(fx.repo / ".git").resolve()}"]' in start
    resume = codex.command({"bin": "cx"}, "more", fx.repo, "T1", s, o)
    assert resume[:4] == ["cx", "exec", "resume", "T1"] and "-C" not in resume and resume[-1] == "more"
    review = codex.command({"bin": "cx"}, "look", fx.repo, None, s, o, readonly=True, profile="qa")
    assert 'sandbox_mode="read-only"' in review and 'profile="qa"' in review


def test_other_commands(tmp_path):
    c = claude.command({"model": "opus"}, tmp_path, "S1", engines.RESULT_SCHEMA, readonly=True, reasoning="high")
    assert c[:2] == ["claude", "-p"] and ["--resume", "S1"] == c[c.index("--resume"):c.index("--resume") + 2]
    assert c[-3:] == claude.READONLY and ["--effort", "high"] == c[c.index("--effort"):c.index("--effort") + 2]
    h = hermes.command({"profile": "coder"}, "go", tmp_path, "S2")
    assert h[:4] == ["hermes", "-p", "coder", "chat"] and h[-2:] == ["--resume", "S2"]
    o = opencode.command({"model": "p/m"}, "go", tmp_path, None)
    assert o[:2] == ["opencode", "run"] and o[-1] == "go" and "--session" not in o
    assert engines.get("coder", {"kind": "hermes"}) is hermes


def test_fenced_and_outcome():
    text = 'All done.\n```json\n{"status": "done", "summary": "ok"}\n```\n'
    assert engines.fenced(text)["summary"] == "ok" and engines.fenced("no block") is None
    assert engines.outcome(None, "You've hit your usage limit", None, {}).status == "rate_limited"
    assert engines.outcome(None, "Not logged in", None, {}).status == "unavailable"
    assert engines.outcome({"status": "done", "summary": "s"}, "", "S", {}).session == "S"


# --- end to end ---

def lanes(extra: dict | None = None, **cfg):
    """A scheduler over lanes from a cpipe.yaml in the temp HERMES_HOME: the round reads the same file."""
    from cpipe.runtime.config import parse_config
    from cpipe.runtime.scheduler import Scheduler
    raw = {"engines": {"codex": {"bin": FAKE, "concurrency": 2}, **(extra or {})}, "review_engine": "codex",
           "env_pass": ["FAKE_CODEX_LOG", "FAKE_CODEX", "FAKE_PLAN", "FAKE_GH_LOG", "FAKE_GH_CHECKS"], **cfg}
    settings.path().write_text(json.dumps(raw))
    return Scheduler(parse_config({**settings.runtime_raw(settings.load()), "heartbeat_interval_seconds": 0.5,
                                   "worker_heartbeat_interval_seconds": 1, "poll_interval_seconds": 0.2}))


def settle(board, sched, tids, want="review"):
    from cpipe.runtime import hermes_api as h
    sched.tick()
    for tid in tids:
        got = wait_for(lambda: h.get_task(board, tid).status not in ("ready", "running")
                       and h.get_task(board, tid).status, 90)
        assert got == want, h.worker_log_path(tid, board=None).read_text()


@pytest.fixture
def repo(tmp_path, hermes_home, monkeypatch):
    """The python fixture without QA (no app servers in tests), fake gh on PATH, fake logs in tmp."""
    fx = make_repo(tmp_path, "python")
    fx.profile["qa"] = "off"
    install([fx], hermes_home)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").symlink_to(HERE / "fake_gh.py")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "codex.jsonl"))
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.jsonl"))
    return fx


def calls(fx, name="codex.jsonl"):
    path = fx.repo.parent / name
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


def test_delivery_end_to_end(board, repo):
    """ship → PR ready to test → feedback → same PR, same session → ready → PR marked ready, card done."""
    from cpipe import report, tools
    from cpipe.runtime import hermes_api as h
    sched = lanes()
    shipped = tools.ship({"request": TASK})
    tid = shipped["task_id"]
    assert shipped["engine"] == "codex" and shipped["branch"].startswith("cp/the-calculator")

    settle(board, sched, [tid])
    builds = [c for c in calls(repo) if not c["readonly"]]
    assert [c["resumed"] for c in builds] == [False, True]     # wrong fix, then the check failure fed back
    assert len({c["thread"] for c in builds}) == 1
    assert any(c["readonly"] and "You review a change" in c["prompt"] for c in calls(repo))
    ws = Path(h.get_task(board, tid).workspace_path)
    state = json.loads((ws / ".cpipe" / "state.json").read_text())
    assert state["pr_url"] == "https://github.com/acme/app/pull/7" and state["verified_head"]
    assert "Review (codex): approved" in state["verification"]
    gh = calls(repo, "gh.jsonl")
    create = next(c for c in gh if c[:2] == ["pr", "create"])
    assert "--draft" in create and "## How to test" in create[create.index("--body") + 1]
    assert str(ws) not in create[create.index("--body") + 1]

    assert tools.command("cp-status", "").startswith(f"{tid}  ")              # the slash commands, too
    assert f"{tid}  ready" in tools.command("cp-feedback", f"{tid} also leave a note in calc.py")
    settle(board, sched, [tid])
    last = [c for c in calls(repo) if not c["readonly"]][-1]
    assert last["resumed"] and last["thread"] == builds[0]["thread"] and "also leave a note" in last["prompt"]
    assert "feedback addressed" in (ws / "calc.py").read_text()
    assert any(c[:2] == ["pr", "edit"] for c in calls(repo, "gh.jsonl"))
    reviews = [c for c in calls(repo) if c["readonly"]]
    assert reviews[-1]["resumed"] and "Review it again" in reviews[-1]["prompt"]

    tools.ready({"task_id": tid})
    settle(board, sched, [tid], want="done")
    assert calls(repo, "gh.jsonl")[-1][:2] == ["pr", "ready"]
    assert len(h.list_cards(board)) == 1
    assert len({c["thread"] for c in calls(repo) if not c["readonly"]}) == 1   # one build session throughout
    row = report.row(board, h.get_task(board, tid))
    assert row["runs"] == 3 and row["tokens"] > 0 and {"build", "checks", "verify", "pr"} <= set(row["nodes"])
    assert "total" in report.render([row])


def test_rate_limit_requeues(board, repo, monkeypatch):
    from cpipe import tools
    from cpipe.runtime import hermes_api as h
    monkeypatch.setenv("FAKE_CODEX", "rate_limit")
    sched = lanes()   # fallback_engine (claude) is not configured: nothing to fall back to
    tid = tools.ship({"request": TASK})["task_id"]
    sched.tick()
    log = h.worker_log_path(tid, board=None)
    wait_for(lambda: "[kanban-worker-exit] rc=" in log.read_text(), 60)
    # Hermes' crash sweep books this exit code as a quota requeue (not a failure)
    assert f"[kanban-worker-exit] rc={h.EXIT_RATE_LIMITED}" in log.read_text()
    assert "[rate_limited]" in log.read_text()


def test_fallback_engine_takes_over(board, repo, monkeypatch, tmp_path):
    from cpipe import tools
    from cpipe.runtime import hermes_api as h
    monkeypatch.setenv("FAKE_CODEX", "rate_limit")
    backup = tmp_path / "fake_backup"
    backup.symlink_to(FAKE)
    sched = lanes({"backup": {"kind": "codex", "bin": str(backup)}}, fallback_engine="backup")
    tid = tools.ship({"request": TASK})["task_id"]
    settle(board, sched, [tid])
    state = json.loads((Path(h.get_task(board, tid).workspace_path) / ".cpipe" / "state.json").read_text())
    assert state["engine"] == "backup" and calls(repo)[0]["bin"] == "fake_codex.py"
    assert {c["bin"] for c in calls(repo)[1:] if not c["readonly"]} == {"fake_backup"}
    assert "Review (backup): approved" in state["verification"]   # the review engine was out too


def test_large_task_splits(board, repo, monkeypatch):
    """A large task is planned in the build session and split into two child deliveries that run in parallel."""
    from cpipe import tools
    from cpipe.runtime import hermes_api as h
    monkeypatch.setenv("FAKE_PLAN", "split")
    sched = lanes()
    tid = tools.ship({"request": TASK, "size": "large"})["task_id"]
    settle(board, sched, [tid], want="done")
    children = [t for t in h.list_cards(board) if t.id != tid]
    assert sorted(t.title for t in children) == ["Part 1", "Part 2"]
    assert all(t.branch_name.startswith(h.get_task(board, tid).branch_name + "-") for t in children)
    monkeypatch.delenv("FAKE_PLAN")
    settle(board, sched, [t.id for t in children])
    for t in children:
        state = json.loads((Path(h.get_task(board, t.id).workspace_path) / ".cpipe" / "state.json").read_text())
        assert state["pr_url"] and "a + b" in (Path(h.get_task(board, t.id).workspace_path) / "calc.py").read_text()


def test_unknown_project_asks_then_saves(board, repo, tmp_path, hermes_home):
    """An unknown project is a question; the answer (a path, or new) is saved to projects.yaml for next time."""
    from cpipe import tools, workspace_prep
    root = tmp_path / "projects"
    sched = lanes(projects_root=str(root))
    asked = tools.ship({"request": "a calculator", "project": "Calc App"})
    assert asked["choices"] == ["new"] and "clarify" in asked["next"] and "task_id" not in asked
    assert "/cp-project Calc App <path> (or new)" in tools.command("cp-ship", "Calc App: a calculator")
    shipped = tools.ship({"request": "a calculator", "project": "Calc App", "repo": "new"})
    assert shipped["project"] == "calc-app"
    assert workspace_prep.projects()["calc-app"]["repo"] == str(root / "calc-app")
    settle(board, sched, [shipped["task_id"]])
    from cpipe.runtime import hermes_api as h
    task = h.get_task(board, shipped["task_id"])
    state = json.loads((Path(task.workspace_path) / ".cpipe" / "state.json").read_text())
    assert state["base"] == "main" and state["notes"]["pr"].startswith("No remote")
    assert "calc.py" in tools._git("-C", str(root / "calc-app"), "show", "--stat", task.branch_name).stdout
    assert tools.resolve_project("Calc App", {"projects_root": str(root)}) == ("calc-app", root / "calc-app")
    (tmp_path / "plain").mkdir()
    assert "not the top of a git repo" in tools.ship({"request": "x", "project": "p", "repo": str(tmp_path / "plain")})["error"]


def test_save_repo_keeps_the_file(hermes_home):
    from cpipe import workspace_prep
    f = hermes_home / "delivery" / "projects.yaml"
    f.parent.mkdir()
    f.write_text("# mine\nprojects:\n  a: &conv\n    base_branch: dev\n  b: *conv\nother: 1\n")
    workspace_prep.save_repo("b", "/r/b")
    workspace_prep.save_repo("c", "/r/c")
    workspace_prep.save_repo("b", "/r/b2")
    got = workspace_prep.projects()
    assert got["a"] == {"base_branch": "dev"} and got["b"] == {"base_branch": "dev", "repo": "/r/b2"}
    assert got["c"] == {"repo": "/r/c"} and f.read_text().startswith("# mine\n") and "other: 1" in f.read_text()
