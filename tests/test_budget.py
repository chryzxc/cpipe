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
    assert cpipe._roles(False) == "plan b · build c · review d, the rest this chat"
    (tmp_path / "cpipe.yaml").write_text("roles:\n  explore: a\n  build: c\n  review: d\n")
    assert cpipe._roles(True) == "build c · review d, the rest this chat, as kanban cards"
    (tmp_path / "cpipe.yaml").write_text("roles:\n  explore: a\n  review: d\n")
    assert cpipe._roles(False) == "explore a · review d, the rest this chat"


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
