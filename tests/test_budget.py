import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("cpipe_plugin", Path(__file__).parents[1] / "__init__.py")
cpipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpipe)


def test_flags_in_any_order():
    assert cpipe._flags("--budget small --kanban fix it") == (True, "small", "fix it", None)
    assert cpipe._flags("fix --budget small") == (False, None, "fix --budget small", None)
    assert cpipe._flags("--budget huge fix it")[3]


def test_setter_keeps_roles_and_replaces_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = tmp_path / "cpipe.yaml"
    config.write_text("roles:\n  build: builder")
    cpipe._budget("small")
    cpipe._budget("large")
    assert config.read_text() == "roles:\n  build: builder\nbudget: large\n"
    assert cpipe._saved_budget() == "large"
    assert "builder" in cpipe._roles(False) and "budget" not in cpipe._roles(False)
    cpipe._budget("off")
    assert cpipe._saved_budget() is None and "budget" not in config.read_text()


def test_one_profile_explores(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "cpipe.yaml").write_text("roles:\n  explore: a\n  plan: b\n  build: c\n  review: d\n")
    assert cpipe._roles(False) == "plan b · build c · review d, the rest on this chat's model"
    (tmp_path / "cpipe.yaml").write_text("roles:\n  explore: a\n  build: c\n  review: d\n")
    assert cpipe._roles(True) == "build c · review d, the rest on this chat's model, as kanban cards"
    (tmp_path / "cpipe.yaml").write_text("roles:\n  explore: a\n  review: d\n")
    assert cpipe._roles(False) == "explore a · review d, the rest on this chat's model"


def test_every_profile_shares_the_roots_file(tmp_path, monkeypatch):
    profile = tmp_path / "profiles" / "any"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    (tmp_path / "cpipe.yaml").write_text("roles:\n  build: c\n")
    assert cpipe._config() == tmp_path / "cpipe.yaml" and "build c" in cpipe._roles(False)
    (profile / "cpipe.yaml").write_text("roles:\n  review: d\n")
    assert cpipe._config() == profile / "cpipe.yaml"


def test_learnings_lists_files_and_user_misses(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert "no learnings yet" in cpipe._learnings()
    (tmp_path / "cpipe" / "learnings").mkdir(parents=True)
    (tmp_path / "cpipe" / "learnings" / "qa.md").write_text("# qa\n- Cover empty lists · 3.55.0 · applied 1 · last 2026-10-27\n")
    (tmp_path / "cpipe" / "history.jsonl").write_text(
        '{"type": "miss", "date": "2026-01-01", "found_by": "user", "step": "qa"}\n'
        '{"type": "run", "date": "2026-10-01"}\n'
        '{"type": "miss", "date": "2026-10-02", "found_by": "user", "step": "qa"}\n'
        '{"type": "miss", "date": "2026-10-02", "found_by": "review", "step": "plan"}\n')
    out = cpipe._learnings()
    assert "qa (1)" in out and "Cover empty lists" in out
    assert out.endswith("Last 1 runs, problems you found after the report: qa 1")


def test_start_message_counts_learned_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert cpipe._learned().startswith("nothing yet")
    (tmp_path / "cpipe" / "learnings").mkdir(parents=True)
    (tmp_path / "cpipe" / "learnings" / "qa.md").write_text("# qa\n- a\n- b\n")
    (tmp_path / "cpipe" / "learnings" / "retired.md").write_text("- old\n")
    assert cpipe._learned().startswith("2 rules from past runs")


def test_mid_run_message_gets_the_steering_reminder():
    start = {"role": "user", "content": cpipe.START.format(skill="s", root="r") + "fix it"}
    step = {"role": "assistant", "content": "✅ 6/11 Build · 29 min · 4 files"}
    report = {"role": "assistant", "content": "✅ Done: fix it\n\nSummary"}
    msg = {"role": "user", "content": "change the PR title"}
    assert cpipe._steering("change the PR title", [start, msg])["context"] == cpipe.STEERING
    assert cpipe._steering([{"type": "text", "text": "merge it"}], [start, step, msg])
    assert cpipe._steering("change the PR title", [start, step, report, msg]) is None
    assert cpipe._steering("/cpipe continue", [start, step]) is None
    assert cpipe._steering("hello", [msg]) is None
    resumed = {"role": "user", "content": cpipe.CONTINUE.format(skill="s", root="r") + "continue"}
    assert cpipe._steering("merge it", [resumed, msg])
    assert cpipe._steering(resumed["content"], [start, step]) is None


def test_continue_reuses_the_loaded_skill():
    class Ctx:
        def inject_message(self, text, **_):
            self.text = text
            return True
    import sys, types
    sys.modules.setdefault("gateway", types.ModuleType("gateway"))
    sys.modules["gateway.session_context"] = types.SimpleNamespace(get_session_env=lambda _: None)
    ctx = Ctx()
    assert cpipe._start(ctx, "continue merge it").startswith("cpipe · resuming")
    assert ctx.text.startswith("Continue the cpipe run") and "only when its text is no longer" in ctx.text
    cpipe._start(ctx, "fix the date filter")
    assert ctx.text.startswith("Load the skill cpipe:run")


def test_rules_are_pinned_only_for_a_running_session(tmp_path, monkeypatch):
    import json
    import sqlite3
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("CPIPE_RUN", raising=False)
    monkeypatch.delenv("CPIPE_STEP", raising=False)
    assert cpipe._pinned({"session_id": "child"}) == ""
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT)")
    db.executemany("INSERT INTO sessions VALUES (?, ?)", [("root", None), ("child", "root")])
    db.commit()
    db.close()
    run = tmp_path / "run"
    run.mkdir()
    (run / "now.md").write_text("## Instructions\n- merge it if no regressions (not yet)\n\n## Step\n7 verify\n")
    (run / "track.json").write_text(json.dumps([
        {"id": "verify-1", "text": "related tests pass", "status": "open"},
        {"id": "verify-2", "text": "build", "status": "done"}]))
    (tmp_path / "cpipe" / "active").mkdir(parents=True)
    (tmp_path / "cpipe" / "active" / "root.json").write_text(json.dumps({"run_dir": str(run)}))
    text = cpipe._pinned({"session_id": "child"})  # compacted twice over: still the run's rules
    assert "loops until the goal" in text and "Root cause first" in text and "merge it if no regressions" in text and "## Step" not in text
    assert "verify-1 related tests pass" in text and "verify-2" not in text and "<skill dir>" not in text
    assert len(text) <= cpipe.PINNED_MAX and cpipe._pinned({"session_id": "other"}) == ""
    (run / "track.json").write_text(json.dumps([{"id": f"x-{i}", "text": "y" * 80, "status": "open"}
                                                for i in range(100)]))
    assert len(cpipe._pinned({"session_id": "root"})) <= cpipe.PINNED_MAX
    monkeypatch.setenv("CPIPE_RUN", str(run))
    assert "Run folder: " + str(run) in cpipe._pinned({"session_id": ""})
    monkeypatch.setenv("CPIPE_STEP", "build")  # a step session: its own rules, never the coordinator's report
    step = cpipe._pinned({"session_id": ""})
    assert "one step of a cpipe run" in step and "Root cause first" in step and "cpipe.py report" not in step
