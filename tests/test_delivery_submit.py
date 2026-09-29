"""Every request is planned by the planner first (small: change + pins; large: after a cheap map),
then built with a same-card review and verified. Missing input never reaches the board."""
import json

from software_delivery import submit


def roster(tmp_path, monkeypatch):
    (tmp_path / "roster.yaml").write_text(
        "roles:\n  implementer: forge   # coder\n  reviewer: sentry\n  planner: archon\n  investigator: scout\n"
        "  verifier: sentinel\n")
    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path)


def test_small_request_starts_with_a_pin_plan_on_the_planner(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    argv, assignee = submit.build_card("Fix icon size", "Make the error icon 16px", "my-app", "small")
    assert assignee == "archon" and argv[2] == "Plan: Fix icon size"
    body = argv[argv.index("--body") + 1]
    assert "Make the error icon 16px" in body
    assert "PINS" in body and "UNPINNED" in body and "Read-only" in body and "token_budget" in body
    assert argv[argv.index("--project") + 1] == "my-app"
    assert argv[argv.index("--workspace") + 1] == "worktree"
    assert int(argv[argv.index("--priority") + 1]) > submit.PRIORITY["large"]


def test_large_request_maps_cheaply_then_plans_from_the_map(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    monkeypatch.setattr(submit, "_roster_gaps", lambda: type("rg", (), {"roster_gaps": staticmethod(lambda **k: {})}))
    calls = []

    def fake_hermes(*argv):
        calls.append(argv)
        return type("P", (), {"returncode": 0, "stdout": json.dumps({"id": f"t_{len(calls)}"}), "stderr": ""})

    monkeypatch.setattr(submit, "_hermes", fake_hermes)
    monkeypatch.setattr(submit, "_subscribe_calling_chat", lambda tid: True)
    result = json.loads(submit.submit({"title": "Add SSO", "request": "Add SSO login",
                                       "project": "my-app", "size": "large"}))
    map_argv, plan_argv, build_argv, verify_argv = calls
    arg = lambda argv, flag: argv[argv.index(flag) + 1]
    assert arg(map_argv, "--assignee") == "scout" and "Read-only" in arg(map_argv, "--body")
    assert arg(plan_argv, "--assignee") == "archon" and arg(plan_argv, "--parent") == "t_1"
    assert "Do not load skills" in arg(plan_argv, "--body")
    assert "my-software-delivery-orchestrator" not in arg(plan_argv, "--body")
    assert arg(build_argv, "--assignee") == "forge" and arg(build_argv, "--parent") == "t_2"
    assert "--reviewer sentry" in arg(build_argv, "--body") and "{" not in arg(build_argv, "--body")
    assert arg(verify_argv, "--assignee") == "sentinel" and arg(verify_argv, "--parent") == "t_3"
    assert arg(verify_argv, "--workspace") == "scratch" and "{" not in arg(verify_argv, "--body")
    assert result["ok"] and result["task_id"] == "t_3"
    assert result["cards"] == {"map": "t_1", "plan": "t_2", "build": "t_3", "verify": "t_4"}


def test_small_request_is_planned_built_then_verified(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    monkeypatch.setattr(submit, "_roster_gaps", lambda: type("rg", (), {"roster_gaps": staticmethod(lambda **k: {})}))
    calls = []
    monkeypatch.setattr(submit, "_hermes", lambda *argv: calls.append(argv) or type(
        "P", (), {"returncode": 0, "stdout": json.dumps({"id": f"t_{len(calls)}"}), "stderr": ""}))
    monkeypatch.setattr(submit, "_subscribe_calling_chat", lambda tid: True)
    result = json.loads(submit.submit({"title": "Fix", "request": "r", "project": "p", "size": "small"}))
    assert result["cards"] == {"plan": "t_1", "build": "t_2", "verify": "t_3"}
    build = calls[1]
    assert build[build.index("--assignee") + 1] == "forge" and build[build.index("--parent") + 1] == "t_1"
    body = build[build.index("--body") + 1]
    assert "PIN, before editing" in body and "--reviewer sentry" in body and "{" not in body


def test_long_request_is_refused_as_not_a_brief(tmp_path, monkeypatch):
    result = json.loads(submit.submit({"title": "T", "request": "x" * 3001, "project": "p", "size": "small"}))
    assert not result["ok"] and "brief" in result["error"]


def _verify_card(monkeypatch, title):
    monkeypatch.setattr(submit, "_task_row", lambda tid: {
        "title": title, "project_id": "my-app",
        "body": submit.VERIFY_BRIEF.format(request="Gate SMS on consent")})
    monkeypatch.setattr(submit, "_parent_worktree", lambda tid: "/repo/.worktrees/t1")
    monkeypatch.setattr(submit, "_copy_subscriptions", lambda a, b: None)


def test_verify_failure_opens_fix_in_same_worktree_and_reverifies(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    _verify_card(monkeypatch, "Verify: Gate SMS")
    calls = []
    monkeypatch.setattr(submit, "_hermes", lambda *argv: calls.append(argv) or type(
        "P", (), {"returncode": 0, "stdout": json.dumps({"id": f"t_{len(calls)}"}), "stderr": ""}))
    result = json.loads(submit.verify_failed({"task_id": "t_v", "failures": "jest: consent.spec fails"}))
    fix, reverify = calls
    arg = lambda argv, flag: argv[argv.index(flag) + 1]
    assert result == {"ok": True, "fix": "t_1", "verify": "t_2", "round": 1,
                      "next": "complete your verify card with the failure evidence"}
    assert arg(fix, "--assignee") == "forge" and arg(fix, "--workspace") == "dir:/repo/.worktrees/t1"
    assert "jest: consent.spec fails" in arg(fix, "--body") and "Gate SMS on consent" in arg(fix, "--body")
    assert fix[2] == "Fix 1: Gate SMS" and arg(fix, "--parent") == "t_v"
    assert reverify[2] == "Verify: Fix 1: Gate SMS" and arg(reverify, "--parent") == "t_1"


def test_verify_failure_stops_after_round_limit(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    _verify_card(monkeypatch, f"Verify: Fix {submit.MAX_FIX_ROUNDS}: Gate SMS")
    monkeypatch.setattr(submit, "_hermes", lambda *a: (_ for _ in ()).throw(AssertionError("called")))
    result = json.loads(submit.verify_failed({"task_id": "t_v", "failures": "still failing"}))
    assert result["ok"] is False and "round limit" in result["error"]


def test_missing_fields_never_create_a_card(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    monkeypatch.setattr(submit, "_hermes", lambda *a: (_ for _ in ()).throw(AssertionError("called")))
    result = json.loads(submit.submit({"title": "x", "request": "", "project": "p", "size": "small"}))
    assert result["ok"] is False


def test_role_without_a_bot_blocks_handoff_and_names_the_fix(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    team = tmp_path / "skills/my-software-delivery-orchestrator/references/team-config.yaml"
    team.parent.mkdir(parents=True)
    team.write_text("bots:\n  implementer: {mission: code}\n  reviewer: {mission: review}\n"
                    "  investigator: {mission: map}\n")
    (tmp_path / "profiles" / "forge").mkdir(parents=True)          # sentry profile is missing
    monkeypatch.setattr(submit, "_hermes", lambda *a: (_ for _ in ()).throw(AssertionError("called")))
    result = json.loads(submit.submit({"title": "x", "request": "y", "project": "p", "size": "small"}))
    assert result["ok"] is False
    assert "reviewer" in result["missing_roles"] and "'sentry' does not exist" in result["missing_roles"]["reviewer"]
    assert "'scout' does not exist" in result["missing_roles"]["investigator"]
    assert "implementer" not in result["missing_roles"]


def test_content_request_is_one_build_card_with_a_wording_review(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    monkeypatch.setattr(submit, "_roster_gaps", lambda: type("rg", (), {"roster_gaps": staticmethod(lambda **k: {})}))
    calls = []
    monkeypatch.setattr(submit, "_hermes", lambda *argv: calls.append(argv) or type(
        "P", (), {"returncode": 0, "stdout": json.dumps({"id": f"t_{len(calls)}"}), "stderr": ""}))
    monkeypatch.setattr(submit, "_subscribe_calling_chat", lambda tid: True)
    result = json.loads(submit.submit({"title": "Add SMS section", "request": "r", "project": "p", "size": "content"}))
    assert result["cards"] == {"build": "t_1"} and len(calls) == 1
    build = calls[0]
    assert build[build.index("--assignee") + 1] == "forge" and "--parent" not in build
    body = build[build.index("--body") + 1]
    assert "No new tests" in body and "--reviewer sentry" in body and "PIN, before editing" not in body


def _fake_board(tmp_path, monkeypatch):
    import sqlite3
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from kanban_board import board, card
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_open", "running", title="Plan: Fix the login redirect", project_id="my-app",
         body="REQUEST: see https://github.com/o/r/issues/12")
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    calls = []
    monkeypatch.setattr(submit, "_hermes", lambda *argv: calls.append(argv) or type(
        "P", (), {"returncode": 0, "stdout": json.dumps({"id": f"t_{len(calls)}"}), "stderr": ""}))
    monkeypatch.setattr(submit, "_subscribe_calling_chat", lambda tid: True)
    monkeypatch.setattr(submit, "_roster_gaps", lambda: type("rg", (), {"roster_gaps": staticmethod(lambda **k: {})}))
    return calls


def test_duplicate_work_returns_the_open_card_instead_of_a_new_chain(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    calls = _fake_board(tmp_path, monkeypatch)
    same_title = json.loads(submit.submit({"title": "fix the login redirect", "request": "x",
                                           "project": "my-app", "size": "small"}))
    same_issue = json.loads(submit.submit({"title": "Other words", "request": "https://github.com/o/r/issues/12",
                                           "project": "other", "size": "small"}))
    assert same_title["duplicate_of"] == same_issue["duplicate_of"] == "t_open" and calls == []
    other_project = json.loads(submit.submit({"title": "Fix the login redirect", "request": "x",
                                              "project": "else", "size": "content"}))
    forced = json.loads(submit.submit({"title": "Fix the login redirect", "request": "x",
                                       "project": "my-app", "size": "content", "force": True}))
    assert other_project["ok"] and forced["ok"]


def test_review_cards_are_refused(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    calls = _fake_board(tmp_path, monkeypatch)
    result = json.loads(submit.submit({"title": "Review PR #4", "request": "review it",
                                       "project": "my-app", "size": "small"}))
    assert not result["ok"] and "review lane" in result["error"] and calls == []


def test_saved_project_conventions_reach_every_card(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    calls = _fake_board(tmp_path, monkeypatch)
    (tmp_path / "delivery").mkdir()
    (tmp_path / "delivery" / "projects.yaml").write_text("projects:\n  my-app:\n    base_branch: develop\n")
    assert json.loads(submit.submit({"title": "Add dark mode", "request": "x", "project": "my-app",
                                     "size": "small"}))["ok"]
    assert calls and all("PRs target `develop`" in c[c.index("--body") + 1] for c in calls)
