"""The checks node: the operator's quality.yaml checks, run on the delivery's changed files only."""
import json
import subprocess

from cpipe import quality


def sh(root, *cmd):
    subprocess.run(cmd, cwd=root, check=True, capture_output=True)


def setup(tmp_path, checks):
    home, ws = tmp_path / "home", tmp_path / "ws"
    (home / "delivery").mkdir(parents=True)
    (home / "delivery/quality.yaml").write_text("checks:\n" + checks)
    ws.mkdir()
    (ws / "a.js").write_text("console.log(0)\n")
    sh(ws, "git", "init", "-q", "-b", "main")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    sh(ws, *git, "add", ".")
    sh(ws, *git, "commit", "-qm", "base")
    sh(ws, "git", "update-ref", "refs/remotes/origin/main", "HEAD")
    (ws / "b.js").write_text("console.log(1)\n")
    (ws / "c.py").write_text("y\n")
    sh(ws, *git, "add", ".")
    sh(ws, *git, "commit", "-qm", "change")
    return home, ws, quality.diff_base(ws, "main")


def test_a_failing_check_returns_its_output_for_changed_files_only(tmp_path):
    home, ws, base = setup(tmp_path, '  - name: nolog\n    files: "*.js"\n    run: "! grep -Hn console.log {files}"\n'
                                     '  - name: py\n    files: "*.py"\n    run: "true {files}"\n')
    [failure] = quality.run_checks(ws, base, "app", home)
    assert failure.startswith("nolog:") and "b.js:1:console.log" in failure and "a.js" not in failure


def test_passing_unmatched_or_other_projects_never_fail(tmp_path):
    home, ws, base = setup(tmp_path, '  - name: ok\n    files: "*.js"\n    run: "true {files}"\n'
                                     '  - name: css\n    files: "*.css"\n    run: "false"\n'
                                     '  - name: other\n    run: "false"\n    projects: [elsewhere]\n')
    assert quality.run_checks(ws, base, "app", home) == []


def test_checks_run_without_hermes_python_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/hermes/venv/site-packages")
    monkeypatch.setenv("PYTHONHOME", "/hermes/python")
    done = quality._run('test -z "$PYTHONPATH$PYTHONHOME"', tmp_path)
    assert done.returncode == 0


def test_a_broken_check_tool_is_skipped_and_reported_until_it_runs(tmp_path):
    home, ws, base = setup(tmp_path, '  - name: sec\n    files: "*.js"\n    run: "sh x.sh"\n'
                                     '  - name: gone\n    run: "no-such-tool-xyz {files}"\n')
    (ws / "x.sh").write_text("echo 'Traceback (most recent call last):'; exit 2\n")
    problems = home / "state" / "setup-problems.json"
    assert quality.run_checks(ws, base, "app", home, "t_1") == []
    assert set(json.loads(problems.read_text())) == {"sec", "gone"}
    assert json.loads(problems.read_text())["sec"]["card"] == "t_1"
    (ws / "x.sh").write_text("exit 0\n")
    assert quality.run_checks(ws, base, "app", home) == []
    assert set(json.loads(problems.read_text())) == {"gone"}
    assert quality.missing_tools("app", home) == ["gone"]


def test_a_check_dies_with_the_worker_that_started_it(tmp_path):
    import os
    import sys
    import time
    pidfile = tmp_path / "pid"
    code = ("import sys; from pathlib import Path; from cpipe import quality; "
            f"quality._run('echo $$ > {pidfile}; sleep 60', Path(sys.argv[1]))")
    worker = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)])
    for _ in range(50):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.1)
    check = int(pidfile.read_text())
    worker.kill()  # the worker dies mid-check, so its own timeout never fires
    worker.wait()
    deadline = time.monotonic() + quality.ORPHAN_POLL + 3
    while time.monotonic() < deadline:
        status = subprocess.run(["ps", "-o", "stat=", "-p", str(check)],
                                capture_output=True, text=True).stdout.strip()
        # Linux may retain a dead orphan as a zombie until PID 1 reaps it.
        # kill(pid, 0) still succeeds then, but the check is no longer running.
        if not status or status.startswith("Z"):
            break
        time.sleep(0.1)
    else:
        try:
            os.killpg(check, 9)
        except ProcessLookupError:
            pass
        raise AssertionError("the check outlived its worker")
    assert quality._run("exit 3", tmp_path).returncode == 3  # a check's own exit code still comes through
