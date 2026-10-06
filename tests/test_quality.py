"""The operator's quality.yaml: checks refuse the implementer's review request; guidance reaches each role's first turn."""
import json
import subprocess
from pathlib import Path

from cpipe import quality, review_gate, submit, workspace_prep


def sh(root, *cmd):
    subprocess.run(cmd, cwd=root, check=True, capture_output=True)


def setup(tmp_path, monkeypatch, checks):
    home, ws = tmp_path / "home", tmp_path / "ws"
    (home / "delivery").mkdir(parents=True)
    (home / "roster.yaml").write_text("roles:\n  implementer: forge\n  reviewer: sentry\n")
    (home / "delivery/quality.yaml").write_text(
        "checks:\n" + checks + "guidance:\n  implementer: Reuse the component library.\n  reviewer: Check UI states.\n")
    ws.mkdir()
    (ws / "a.js").write_text("x\n")
    sh(ws, "git", "init", "-q", "-b", "main")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    sh(ws, *git, "add", ".")
    sh(ws, *git, "commit", "-qm", "base")
    sh(ws, "git", "update-ref", "refs/remotes/origin/main", "HEAD")
    (ws / "b.js").write_text("console.log(1)\n")
    (ws / "c.py").write_text("y\n")
    sh(ws, *git, "add", ".")
    sh(ws, *git, "commit", "-qm", "change")
    monkeypatch.setattr(submit, "HERMES_HOME", home)
    monkeypatch.setattr(quality, "_blocked", {})
    monkeypatch.setattr(workspace_prep, "card_profile", lambda *a, **k: {"base_branch": "main"})
    row = {"assignee": "forge", "body": review_gate.MARKER, "workspace_path": str(ws), "project_id": "app"}
    monkeypatch.setattr(review_gate, "_task", lambda tid: row)
    return home, row


def test_failing_check_refuses_review_with_its_output_then_lets_it_through(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, '  - name: nolog\n    files: "*.js"\n    run: "! grep -Hn console.log {files}"\n'
                                 '  - name: py\n    files: "*.py"\n    run: "true {files}"\n')
    for name, args in (("kanban_request_review", {"task_id": "t_1"}),
                       ("terminal", {"command": "hermes kanban request-review t_1 --reviewer sentry"})):
        verdict = review_gate.gate(tool_name=name, args=args)
        assert verdict["action"] == "block" and "nolog" in verdict["message"] and "b.js:1:console.log" in verdict["message"]
        assert "a.js" not in verdict["message"]  # only files the change touched
    assert review_gate.gate(tool_name="kanban_request_review", args={"task_id": "t_1"})["action"] == "block"
    assert review_gate.gate(tool_name="kanban_request_review", args={"task_id": "t_1"}) is None  # after MAX_BLOCKS


def test_passing_unmatched_or_other_roles_never_block(tmp_path, monkeypatch):
    _, row = setup(tmp_path, monkeypatch, '  - name: ok\n    files: "*.js"\n    run: "true {files}"\n'
                                          '  - name: css\n    files: "*.css"\n    run: "false"\n'
                                          '  - name: other\n    run: "false"\n    projects: [elsewhere]\n')
    assert review_gate.gate(tool_name="kanban_request_review", args={"task_id": "t_1"}) is None
    row["assignee"] = "sentry"
    (submit.HERMES_HOME / "delivery/quality.yaml").write_text("checks:\n  - name: no\n    run: 'false'\n")
    assert review_gate.gate(tool_name="kanban_request_review", args={"task_id": "t_1"}) is None


def test_guidance_goes_to_the_profiles_role(tmp_path, monkeypatch):
    home, _ = setup(tmp_path, monkeypatch, '  - name: lint\n    run: "git diff --check {files}"\n')
    forge = quality.first_turn_notes("forge", "app", home)
    assert forge[0] == "Reuse the component library." and "lint: `git diff --check {files}`" in forge[1]
    assert quality.first_turn_notes("sentry", "app", home) == ["Check UI states."]
    assert quality.first_turn_notes("nobody", "app", home) == []
    assert quality.first_turn_notes("forge", "app", tmp_path / "unset") == []


def test_checks_run_without_hermes_python_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/hermes/venv/site-packages")
    monkeypatch.setenv("PYTHONHOME", "/hermes/python")
    done = quality._run('test -z "$PYTHONPATH$PYTHONHOME"', tmp_path)
    assert done.returncode == 0


def test_a_broken_check_tool_is_skipped_and_reported_until_it_runs(tmp_path, monkeypatch):
    home, _ = setup(tmp_path, monkeypatch, '  - name: sec\n    files: "*.js"\n    run: "sh x.sh"\n'
                                           '  - name: gone\n    run: "no-such-tool-xyz {files}"\n')
    ws = tmp_path / "ws"
    (ws / "x.sh").write_text("echo 'Traceback (most recent call last):'; exit 2\n")
    problems = home / "state" / "setup-problems.json"
    assert review_gate.gate(tool_name="kanban_request_review", args={"task_id": "t_1"}) is None
    assert set(json.loads(problems.read_text())) == {"sec", "gone"}
    assert json.loads(problems.read_text())["sec"]["card"] == "t_1"
    (ws / "x.sh").write_text("exit 0\n")
    assert review_gate.gate(tool_name="kanban_request_review", args={"task_id": "t_1"}) is None
    assert set(json.loads(problems.read_text())) == {"gone"}
    assert quality.missing_tools("app", home) == ["gone"]
    notes = quality.first_turn_notes("forge", "app", home)
    assert any("cannot run on this machine" in n and "gone" in n for n in notes)
    assert "no-such-tool" not in notes[-1]  # not listed as a check to run


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
    for _ in range(50):
        try:
            os.kill(check, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.killpg(check, 9)
        raise AssertionError("the check outlived its worker")
    assert quality._run("exit 3", tmp_path).returncode == 3  # a check's own exit code still comes through
