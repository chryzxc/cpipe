"""A feature is one card: the implementer plans, builds, and opens a PR; the reviewer checks the same
card, then a verifier runs the tests. Large work is mapped and planned first. Missing input never reaches the board."""
import json

from cpipe import submit


def roster(tmp_path, monkeypatch):
    (tmp_path / "roster.yaml").write_text(
        "roles:\n  implementer: forge   # coder\n  reviewer: sentry\n  planner: archon\n  investigator: scout\n"
        "  verifier: sentinel\n")
    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path)


def fake_board(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    monkeypatch.setattr(submit, "_roster_gaps", lambda: type("rg", (), {"roster_gaps": staticmethod(lambda **k: {})}))
    calls = []
    monkeypatch.setattr(submit, "_hermes", lambda *argv: calls.append(argv) or type(
        "P", (), {"returncode": 0, "stdout": json.dumps({"id": f"t_{len(calls)}"}), "stderr": ""}))
    monkeypatch.setattr(submit, "_subscribe_calling_chat", lambda tid: True)
    return calls


arg = lambda argv, flag: argv[argv.index(flag) + 1]  # noqa: E731


def test_small_request_goes_straight_to_the_implementer(tmp_path, monkeypatch):
    calls = fake_board(tmp_path, monkeypatch)
    result = json.loads(submit.submit({"title": "Fix icon size", "request": "Make the error icon 16px",
                                       "project": "my-app", "size": "small", "verify": False}))
    assert result["cards"] == {"build": "t_1"} and result["task_id"] == "t_1"
    [build] = calls
    assert arg(build, "--assignee") == "forge" and build[2] == "Fix icon size" and "--parent" not in build
    assert arg(build, "--workspace") == "worktree" and arg(build, "--max-runtime") == "60m"
    assert int(arg(build, "--priority")) == submit.PRIORITY["small"] > submit.PRIORITY["large"]
    body = arg(build, "--body")
    assert "Make the error icon 16px" in body and "{" not in body
    assert "plan in one\n   pass yourself" in body and "PIN, MODIFIED code only" in body and "--reviewer sentry" in body
    assert "FINISH, DON'T STOP" in body and "gh pr create --draft" in body and "## How to test" in body and "gh pr edit" in body and "linter" in body
    assert "gh pr ready" in body and "3 review rounds" in body and "patch-id" in body
    assert "readFileSync" in body and "never approve over it" in body and "did not ask for" in body
    assert "PLAN FORMAT" in body and "Walk the plan" in body


def test_fix_card_waits_for_live_cards_in_its_worktree(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from kanban_board import board, card, link
    conn = board(tmp_path / "kanban.db")
    card(conn, "t_build", "done", workspace_path="/wt")
    card(conn, "t_verify", "running", assignee="sentinel", workspace_path="/scratch")  # works in its parent's /wt
    card(conn, "t_fix", "review", workspace_path="/wt")
    card(conn, "t_stuck", "blocked", workspace_path="/wt")  # waits on the user: not live
    card(conn, "t_other", "running", workspace_path="/wt2")
    link(conn, "t_build", "t_verify")
    conn.commit()
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    argv = ["kanban", "create", "Fix 2: x", "--workspace", "dir:/wt", "--parent", "t_verify"]
    assert submit._wait_for_worktree(argv) == argv + ["--parent", "t_fix"]
    fresh = ["kanban", "create", "x", "--workspace", "worktree"]
    assert submit._wait_for_worktree(fresh) == fresh


def test_verify_card_by_default(tmp_path, monkeypatch):
    calls = fake_board(tmp_path, monkeypatch)
    result = json.loads(submit.submit({"title": "Fix", "request": "r", "project": "p", "size": "small"}))
    assert result["cards"] == {"build": "t_1", "verify": "t_2"}
    assert arg(calls[1], "--assignee") == "sentinel" and arg(calls[1], "--parent") == "t_1"
    assert "PLATFORM" in arg(calls[1], "--body") and "gh pr ready" in arg(calls[1], "--body")
    assert "PRE-EXISTING" in arg(calls[1], "--body") and "NO BROWSER OR LIVE QA" in arg(calls[1], "--body")
    content = json.loads(submit.submit({"title": "Copy", "request": "r", "project": "p", "size": "content"}))
    assert "verify" not in content["cards"]


def test_large_request_maps_cheaply_then_plans_from_the_map(tmp_path, monkeypatch):
    calls = fake_board(tmp_path, monkeypatch)
    result = json.loads(submit.submit({"title": "Add SSO", "request": "Add SSO login",
                                       "project": "my-app", "size": "large", "verify": False}))
    map_argv, plan_argv, build_argv = calls
    assert arg(map_argv, "--assignee") == "scout" and "Read-only" in arg(map_argv, "--body")
    assert arg(plan_argv, "--assignee") == "archon" and arg(plan_argv, "--parent") == "t_1"
    assert "Do not load skills" in arg(plan_argv, "--body")
    assert "my-software-delivery-orchestrator" not in arg(plan_argv, "--body")
    assert arg(build_argv, "--assignee") == "forge" and arg(build_argv, "--parent") == "t_2"
    assert arg(build_argv, "--max-runtime") == "60m" and arg(plan_argv, "--max-runtime") == "20m"
    assert "--reviewer sentry" in arg(build_argv, "--body") and "{" not in arg(build_argv, "--body")
    assert result["ok"] and result["task_id"] == "t_3"
    assert result["cards"] == {"map": "t_1", "plan": "t_2", "build": "t_3"}


def _built_card(monkeypatch, worktree="/repo/.worktrees/t1"):
    body = submit.BUILD_BRIEF.format(request="Gate SMS on consent", reviewer="sentry")
    monkeypatch.setattr(submit, "_task_row", lambda tid: {"title": "Gate SMS", "project_id": "my-app", "body": body})
    monkeypatch.setattr(submit, "_card_worktree", lambda tid: worktree)
    monkeypatch.setattr(submit, "_card_for_pr", lambda url: "t_b" if url.endswith("/pull/7") else None)
    monkeypatch.setattr(submit, "_copy_subscriptions", lambda a, b: None)


def test_fix_of_a_pr_reopens_the_same_worktree(tmp_path, monkeypatch):
    calls = fake_board(tmp_path, monkeypatch)
    _built_card(monkeypatch)
    result = json.loads(submit.submit({"fix_of": "https://github.com/o/r/pull/7",
                                       "request": "toggle does not save"}))
    assert result["ok"] and result["fix_of"] == "t_b" and result["verify"] is None
    [fix] = calls  # the user re-tests their own feedback: no verify card unless asked
    assert fix[2] == "Fix 1: Gate SMS" and arg(fix, "--assignee") == "forge"
    assert arg(fix, "--workspace") == "dir:/repo/.worktrees/t1"
    body = arg(fix, "--body")
    assert "toggle does not save" in body and "Gate SMS on consent\n\nFAILURES" in body
    assert "--reviewer sentry" in body and "{" not in body and "FIX BASE: unknown" in body


def test_fix_of_with_verify_adds_a_verify_card(tmp_path, monkeypatch):
    calls = fake_board(tmp_path, monkeypatch)
    _built_card(monkeypatch)
    result = json.loads(submit.submit({"fix_of": "t_b", "request": "broken", "verify": True}))
    assert result["verify"] == "t_2" and calls[1][2] == "Verify: Fix 1: Gate SMS" and arg(calls[1], "--parent") == "t_1"


def test_correction_joins_a_fix_card_that_has_not_started(tmp_path, monkeypatch):
    calls = fake_board(tmp_path, monkeypatch)
    _built_card(monkeypatch)
    monkeypatch.setattr(submit, "_waiting_fix", lambda title, project: "t_wait" if title == "Gate SMS" else None)
    result = json.loads(submit.submit({"fix_of": "t_b", "request": "also make it single-select"}))
    assert result["merged_into"] == "t_wait"
    [comment] = calls
    assert comment[:3] == ("kanban", "comment", "t_wait") and "single-select" in comment[3]


def test_fix_of_unknown_pr_creates_nothing(tmp_path, monkeypatch):
    calls = fake_board(tmp_path, monkeypatch)
    _built_card(monkeypatch)
    result = json.loads(submit.submit({"fix_of": "https://github.com/o/r/pull/8", "request": "broken"}))
    assert result["ok"] is False and calls == []


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


def test_worker_profile_home_resolves_to_the_root(monkeypatch, tmp_path):
    from cpipe import home
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "sentinel"))  # how workers run
    assert home.root() == tmp_path  # roster.yaml lives here, not in the profile
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert home.root() == tmp_path
