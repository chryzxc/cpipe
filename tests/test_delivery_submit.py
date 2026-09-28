"""A small request becomes ONE high-priority card on the implementer with a same-card review
hand-off; a large one goes to the planner. Missing input never reaches the board."""
import json

from software_delivery import submit


def roster(tmp_path, monkeypatch):
    (tmp_path / "roster.yaml").write_text(
        "roles:\n  implementer: forge   # coder\n  reviewer: sentry\n  planner: archon\n  investigator: scout\n")
    monkeypatch.setattr(submit, "HERMES_HOME", tmp_path)


def test_small_request_is_one_fast_path_card_with_same_card_review(tmp_path, monkeypatch):
    roster(tmp_path, monkeypatch)
    argv, assignee = submit.build_card("Fix icon size", "Make the error icon 16px", "climaterx", "small")
    assert assignee == "forge"
    body = argv[argv.index("--body") + 1]
    assert "Make the error icon 16px" in body
    assert "--reviewer sentry" in body and "SAME card" in body and "token_budget" in body
    assert argv[argv.index("--project") + 1] == "climaterx"
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
                                       "project": "climaterx", "size": "large"}))
    map_argv, plan_argv, build_argv = calls
    arg = lambda argv, flag: argv[argv.index(flag) + 1]
    assert arg(map_argv, "--assignee") == "scout" and "Read-only" in arg(map_argv, "--body")
    assert arg(plan_argv, "--assignee") == "archon" and arg(plan_argv, "--parent") == "t_1"
    assert "Do not load skills" in arg(plan_argv, "--body")
    assert "my-software-delivery-orchestrator" not in arg(plan_argv, "--body")
    assert arg(build_argv, "--assignee") == "forge" and arg(build_argv, "--parent") == "t_2"
    assert "--reviewer sentry" in arg(build_argv, "--body") and "{" not in arg(build_argv, "--body")
    assert result["ok"] and result["cards"] == {"map": "t_1", "plan": "t_2", "build": "t_3"}


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
