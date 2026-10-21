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
    assert cpipe._roles(False) == "Roles: plan b (explores too) · build c · review d; the rest in this chat."
    (tmp_path / "cpipe.yaml").write_text("roles:\n  explore: a\n  build: c\n  review: d\n")
    assert cpipe._roles(False) == "Roles: build c (explores and plans too) · review d; the rest in this chat."
    (tmp_path / "cpipe.yaml").write_text("roles:\n  explore: a\n  review: d\n")
    assert cpipe._roles(False) == "Roles: explore a · review d; the rest in this chat."
