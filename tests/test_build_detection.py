import json
import runpy
import subprocess
import sys
from pathlib import Path

from software_delivery import _mutation_check

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workflow" / "scripts"))

from buildcmds import detect_build  # noqa: E402


def make_worktree(tmp_path, files):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").mkdir()
    for name, content in files.items():
        p = wt / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return wt


def test_detection_matrix(tmp_path):
    assert detect_build(tmp_path) is None

    swift = tmp_path / "swift_ws"
    swift.mkdir()
    (swift / "Package.swift").write_text("// swift package")
    recipe = detect_build(swift)
    assert recipe["stack"] == "swift"
    assert recipe["build"] == ["swift", "build", "--disable-automatic-resolution"]
    assert recipe["test"] == ["swift", "test", "--disable-automatic-resolution"]
    assert recipe["filter_flag"] == "--filter"

    py = tmp_path / "py_ws"
    py.mkdir()
    (py / "pyproject.toml").write_text("[project]\nname='x'\n")
    recipe = detect_build(py)
    assert recipe["stack"] == "python"
    assert recipe["build"] is None
    assert recipe["test"] == ["python3", "-m", "pytest"]
    assert recipe["filter_flag"] == "-k"

    node = tmp_path / "node_ws"
    node.mkdir()
    (node / "package.json").write_text('{"scripts": {"test": "jest"}}')
    recipe = detect_build(node)
    assert recipe["stack"] == "node"
    assert recipe["test"] == ["npm", "test"]

    bare_node = tmp_path / "node_bare"
    bare_node.mkdir()
    (bare_node / "package.json").write_text('{"scripts": {"start": "x"}}')
    assert detect_build(bare_node) is None

    mk = tmp_path / "make_ws"
    mk.mkdir()
    (mk / "Makefile").write_text("test:\n\techo ok\n")
    assert detect_build(mk)["stack"] == "make"


def test_mutation_error_when_no_recipe(tmp_path):
    wt = make_worktree(tmp_path, {"src.swift": "a == b"})
    result = json.loads(_mutation_check(str(wt), "src.swift", "T"))
    assert result["ok"] is False
    assert "test_cmd" in result["error"]


def test_mutation_check_generic_python_path_end_to_end(tmp_path):
    wt = make_worktree(tmp_path, {
        "pyproject.toml": "[project]\nname = 'fixture'\n",
        "calc.py": "def ok():\n    return 1 == 1\n",
        "test_calc.py": "from calc import ok\n\ndef test_ok():\n    assert ok()\n",
    })

    result = json.loads(_mutation_check(str(wt), "calc.py", "test_ok"))

    assert result["ok"] is True
    assert result["stack"] == "python"
    assert result["mutant_killed"] is True
    assert (wt / "calc.py").read_text() == "def ok():\n    return 1 == 1\n"


def test_mutation_check_test_cmd_override(tmp_path):
    wt = make_worktree(tmp_path, {
        "calc.py": "def ok():\n    return 1 == 1\n",
        "test_calc.py": "from calc import ok\n\ndef test_ok():\n    assert ok()\n",
    })

    result = json.loads(_mutation_check(
        str(wt), "calc.py", "test_ok", test_cmd=f"{sys.executable} -m pytest -q"))

    assert result["ok"] is True
    assert result["stack"] == "custom"
    assert result["mutant_killed"] is True


def test_warm_build_uses_detected_recipe(monkeypatch, tmp_path):
    warm_build = ROOT / "workflow" / "scripts" / "warm_build_scan.py"
    hermes_home = tmp_path / "hermes"
    (hermes_home / "kanban" / "logs").mkdir(parents=True)
    import sqlite3
    import time

    conn = sqlite3.connect(hermes_home / "kanban.db")
    conn.executescript(
        "CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, assignee TEXT,"
        " skills TEXT, created_at REAL, started_at REAL, last_heartbeat_at REAL,"
        " workspace_path TEXT, last_failure_error TEXT);")
    swift_ws = tmp_path / "swift tree"
    swift_ws.mkdir()
    (swift_ws / "Package.swift").write_text("// swift package")
    conn.execute(
        "INSERT INTO tasks VALUES ('w1','x','todo','forge','[]',?,NULL,NULL,?,NULL)",
        (time.time(), str(swift_ws)))
    py_ws = tmp_path / "py tree"
    py_ws.mkdir()
    (py_ws / "pyproject.toml").write_text("[project]\nname='x'\n")
    conn.execute(
        "INSERT INTO tasks VALUES ('w2','x','todo','forge','[]',?,NULL,NULL,?,NULL)",
        (time.time(), str(py_ws)))
    conn.commit()
    conn.close()

    launched = []

    def fake_popen(command, **kwargs):
        launched.append((command, kwargs))

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    runpy.run_path(str(warm_build))

    assert len(launched) == 1  # only the swift workspace warm-builds
    command = launched[0][0]
    assert command[0] == sys.executable
    assert str(swift_ws) in command[3]
    assert json.loads(command[6]) == ["swift", "build", "--disable-automatic-resolution"]
