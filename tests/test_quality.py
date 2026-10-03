"""The operator's quality.yaml: checks refuse the implementer's review request; guidance reaches each role's first turn."""
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
    home, _ = setup(tmp_path, monkeypatch, '  - name: lint\n    run: "eslint {files}"\n')
    forge = quality.first_turn_notes("forge", "app", home)
    assert forge[0] == "Reuse the component library." and "lint: `eslint {files}`" in forge[1]
    assert quality.first_turn_notes("sentry", "app", home) == ["Check UI states."]
    assert quality.first_turn_notes("nobody", "app", home) == []
    assert quality.first_turn_notes("forge", "app", tmp_path / "unset") == []
