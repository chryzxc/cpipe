"""Profile handoffs through real workers, subprocesses and local git; no actual models, PRs, or live board."""
import os
from pathlib import Path

from cpipe import report, tools
from cpipe.runtime import hermes_api
from fixtures.make_repo import TASK, install, make_repo
from test_round import calls, lanes, settle


def test_profiled_delivery_end_to_end(board, tmp_path, hermes_home, monkeypatch):
    fx = make_repo(tmp_path, "python")
    fx.profile["qa"] = "off"
    install([fx], hermes_home)
    here = Path(__file__).parent
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").symlink_to(here / "fake_gh.py")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    for var, filename in (("FAKE_HERMES_LOG", "hermes.jsonl"), ("FAKE_CODEX_LOG", "codex.jsonl"), ("FAKE_GH_LOG", "gh.jsonl")):
        monkeypatch.setenv(var, str(tmp_path / filename))
    fake = str(here / "fake_hermes.py")
    cfg = {"builder": {"kind": "hermes", "bin": fake, "profile": "forge", "concurrency": 2},
           "planner": {"kind": "hermes", "bin": fake, "profile": "archon"},
           "reviewer": {"kind": "hermes", "bin": fake, "profile": "sentry"}}
    sched = lanes(cfg, default_engine="builder", roles={"plan": "planner", "build": "builder", "review": "reviewer"},
                  env_pass=["FAKE_HERMES_LOG", "FAKE_CODEX_LOG", "FAKE_GH_LOG"], review_engine="reviewer")
    tid = tools.plan({"request": TASK})["task_id"]
    settle(board, sched, [tid])
    tools.build({"task_id": tid})
    settle(board, sched, [tid])
    turns = calls(fx, "hermes.jsonl")
    assert [c["profile"] for c in turns] == ["archon", "forge", "forge", "forge", "sentry"]
    assert [c["resumed"] for c in turns] == [False, False, True, True, False]
    assert len({c["session"] for c in turns}) == 3
    assert "Make add add" in turns[1]["prompt"]
    task = hermes_api.get_task(board, tid)
    assert task and task.workspace_path
    assert "a + b" in (Path(task.workspace_path) / "calc.py").read_text()
    st = tools.status({"task_id": tid})
    assert st["pr"] and st["stage"].startswith("ready to test")
    row = report.row(board, hermes_api.get_task(board, tid))
    assert row["agents"] == ["archon", "forge", "sentry"]
    assert row["agent_calls"] == 5 and row["tokens"] == 5250 and row["cached_input_tokens"] == 4000
    assert row["calls_without_usage"] == 0
