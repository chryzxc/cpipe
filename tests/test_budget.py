import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("cpipe_plugin", Path(__file__).parents[1] / "__init__.py")
cpipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpipe)


def test_flags_in_any_order():
    assert cpipe._flags("--budget small --kanban fix it") == ({"--kanban"}, "small", "fix it", None)
    assert cpipe._flags("--parallel fix #1 and #2") == ({"--parallel"}, None, "fix #1 and #2", None)
    assert cpipe._flags("fix --budget small") == (set(), None, "fix --budget small", None)
    assert cpipe._flags("--spec --budget small fix it") == ({"--spec"}, "small", "fix it", None)
    assert cpipe._flags("--budget huge fix it")[3]


def test_setter_keeps_roles_and_replaces_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = tmp_path / "cpipe.yaml"
    config.write_text("roles:\n  build: builder")
    cpipe._budget("small")
    cpipe._budget("large")
    assert config.read_text() == "roles:\n  build: builder\nbudget: large\n"
    assert cpipe._saved_budget() == "large"
    assert cpipe._roles()[0] == ("build", "builder") and len(cpipe._roles()) == 2
    cpipe._budget("off")
    assert cpipe._saved_budget() is None and "budget" not in config.read_text()


def test_one_profile_explores(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "cpipe.yaml").write_text("roles:\n  explore: a\n  plan: b\n  build: c\n  review: d\n")
    rest = ("everything else", "this chat's model")
    assert cpipe._roles() == [("plan", "b"), ("build", "c"), ("review", "d"), rest]
    (tmp_path / "cpipe.yaml").write_text("roles:\n  review: d\n  build: c\n  explore: a\n")
    assert cpipe._roles() == [("build", "c"), ("review", "d"), rest]
    (tmp_path / "cpipe.yaml").write_text("roles:\n  explore: a\n  review: d\n")
    assert cpipe._team().splitlines()[2:] == ["| explore | a |", "| review | d |", "| everything else | this chat's model |"]


def test_every_profile_shares_the_roots_file(tmp_path, monkeypatch):
    profile = tmp_path / "profiles" / "any"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    (tmp_path / "cpipe.yaml").write_text("roles:\n  build: c\n")
    assert cpipe._config() == tmp_path / "cpipe.yaml" and ("build", "c") in cpipe._roles()
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
    assert "**qa** (1)" in out and "Cover empty lists" in out
    assert out.endswith("Last 1 runs, problems you found after the report: qa 1")


def test_start_message_counts_learned_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert cpipe._learned().startswith("nothing yet")
    (tmp_path / "cpipe" / "learnings").mkdir(parents=True)
    (tmp_path / "cpipe" / "learnings" / "qa.md").write_text("# qa\n- a\n- b\n")
    (tmp_path / "cpipe" / "learnings" / "retired.md").write_text("- old\n")
    assert cpipe._learned().startswith("2 rules from past runs")


def test_mid_run_message_gets_the_steering_reminder(tmp_path, monkeypatch):
    monkeypatch.setenv("CPIPE_RUN", str(tmp_path))
    start = {"role": "user", "content": cpipe.START.format(skill="s", root="r") + "fix it"}
    step = {"role": "assistant", "content": "✅ 6/11 Build · 29 min · 4 files"}
    report = {"role": "assistant", "content": "✅ Done: fix it\n\nSummary"}
    msg = {"role": "user", "content": "change the PR title"}
    assert cpipe._steering("change the PR title", [start, msg])["context"] == cpipe.STEERING
    assert cpipe._steering([{"type": "text", "text": "merge it"}], [start, step, msg])
    assert cpipe._steering("change the PR title", [start, step, report, msg]) is None
    assert cpipe._steering("/cpipe continue", [start, step]) is None
    assert cpipe._steering("hello", [msg]) is None
    resumed = {"role": "user", "content": cpipe.CONTINUE.format(skill="s", root="r", version="1", verdict="") + "continue"}
    assert cpipe._steering("merge it", [resumed, msg])
    assert cpipe._steering(resumed["content"], [start, step]) is None
    # A closed run (its report passed, unregistered) steers nothing, even when the chat's report line was off.
    monkeypatch.delenv("CPIPE_RUN")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert cpipe._steering("make the history a modal", [start, step, msg], session_id="s1") is None


def test_continue_reuses_the_loaded_skill():
    class Ctx:
        def inject_message(self, text, **_):
            self.text = text
            return True
    import sys, types
    sys.modules.setdefault("gateway", types.ModuleType("gateway"))
    sys.modules["gateway.session_context"] = types.SimpleNamespace(get_session_env=lambda _: None)
    ctx = Ctx()
    assert cpipe._start(ctx, "continue merge it").startswith("**cpipe** · resuming")
    assert ctx.text.startswith("Continue the cpipe run") and f"has its version {cpipe._version()}" in ctx.text
    assert cpipe.SCOPE in ctx.text  # the user's answer to "may I go beyond the named fix?" travels with the run
    cpipe._start(ctx, "fix the date filter")
    assert ctx.text.startswith("Load the skill cpipe:run") and cpipe.SCOPE in ctx.text
    assert "Lanes" in cpipe._start(ctx, "--parallel fix #1 and #2") and ctx.text.endswith("--parallel fix #1 and #2")


def test_a_dash_typed_for_two_hyphens_is_still_the_flag():
    class Ctx:
        def inject_message(self, text, **_):
            self.text = text
            return True
    import sys, types
    sys.modules.setdefault("gateway", types.ModuleType("gateway"))
    sys.modules["gateway.session_context"] = types.SimpleNamespace(get_session_env=lambda _: None)
    ctx = Ctx()
    assert "restarting from **build**" in cpipe._start(ctx, "—restart build")
    assert ctx.text.startswith("Continue the cpipe run") and ctx.text.endswith("--restart build")
    assert "Lanes" in cpipe._start(ctx, "–parallel fix #1 and #2")
    assert cpipe._start(ctx, "fix the A—B toggle").startswith("**cpipe** · fix the A—B toggle")


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
    (tmp_path / "cpipe" / "active" / "root.json").write_text(cpipe.json.dumps({"run_dir": str(run)}))
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


def test_a_turn_cannot_end_on_a_question_while_the_gate_has_work(tmp_path, monkeypatch):
    """'Needs your decision' with the goal's tests still failing went back to the loop, not to the user."""
    import json
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("CPIPE_RUN", raising=False)
    monkeypatch.delenv("CPIPE_STEP", raising=False)
    monkeypatch.delenv("CPIPE_LANE", raising=False)
    run = tmp_path / "run"
    run.mkdir()
    (run / "track.json").write_text(json.dumps([{"id": "verify-1", "text": "race tests pass", "status": "open",
                                                 "evidence": ""}]))
    (tmp_path / "cpipe" / "active").mkdir(parents=True)
    (tmp_path / "cpipe" / "active" / "s1.json").write_text(cpipe.json.dumps({"run_dir": str(run)}))
    ask = "⚠️ Needs your decision: authorize a broader approach or accept the risk?"
    nudge = cpipe._unfinished(session_id="s1", final_response=ask)
    assert "verify-1 race tests pass" in cpipe._verdict("s1")  # a continue starts from the gate, not the last reply
    assert nudge["action"] == "continue" and "verify-1 race tests pass" in nudge["message"]
    wait = "⏳ 6/11 Build · waiting on x · done by 1:00 · resumes on its own"
    import types, sys
    running = []  # Hermes' snapshot of this chat's background processes
    goals = types.SimpleNamespace(gather_background_processes=lambda owner_task_id=None: running)
    monkeypatch.setitem(sys.modules, "hermes_cli.goals", goals)
    assert "nothing this chat started is running" in cpipe._unfinished(session_id="s1", final_response=wait)["message"]
    running.append({"pid": 1, "status": "running"})
    assert cpipe._unfinished(session_id="s1", final_response=wait) is None
    assert cpipe._unfinished(session_id="other", final_response=ask) is None  # no run in that chat
    monkeypatch.setenv("CPIPE_STEP", "build")  # a role's own session ends on its reply
    assert cpipe._unfinished(session_id="s1", final_response=ask) is None


def test_the_watcher_queues_the_next_turn_until_the_run_is_done(tmp_path, monkeypatch):
    import sys, types
    sys.modules.setdefault("gateway", types.ModuleType("gateway"))
    sys.modules["gateway.session_context"] = types.SimpleNamespace(get_session_env=lambda _: None)
    run = tmp_path / "run"
    run.mkdir()
    verdict = {"v": "FIX: R1 has no proof"}
    monkeypatch.setattr(cpipe, "_run_dir", lambda _: run)
    monkeypatch.setattr(cpipe, "_gate", lambda *_: verdict["v"])
    monkeypatch.setattr(cpipe, "_waiting", lambda _: False)
    monkeypatch.delenv("CPIPE_STEP", raising=False)
    monkeypatch.delenv("CPIPE_LANE", raising=False)

    class Ctx:
        text = None
        def inject_message(self, text, **_):
            self.text = text
            return True
    started = [{"role": "user", "content": cpipe.START.format(skill="s", root="r") + "fix it"}]

    def turn(reply, user="go"):
        ctx = Ctx()
        cpipe._watch(ctx, session_id="s", user_message=user, assistant_response=reply,
                     conversation_history=started + [{"role": "assistant", "content": reply}])
        return ctx.text

    assert "Do the next item" in turn("I could not finish R1, here is a summary.")
    assert "turn limit" in turn("⏳ Paused by the turn limit at 6/11 build, not finished: x")
    assert turn("ok, stopping", user="please stop the run") is None  # the user's stop wins
    for _ in range(4):
        last = turn("same summary")
    assert "Change it" in last  # nudges that change nothing ask for another approach
    for _ in range(cpipe.GIVE_UP):
        last = turn("same summary")
    assert last is None  # and never loop forever
    verdict["v"] = "ASK: answer: R2"
    assert turn("❓ 6/11 Build · waiting on your answer · R2: which?") is None
    assert "❓" in turn("Still working.")
    verdict["v"] = "PASS: every item done"
    assert "write the report" in turn("All done!")
    assert turn("✅ Done · fix it") is None



class _Chat:
    def __init__(self):
        self.sent = []

    def inject_message(self, text, role, session_key):
        self.sent.append(text)
        return True


def test_rules_are_shown_edited_in_words_and_pinned_by_scope(tmp_path, monkeypatch):
    root, repo = tmp_path / "hermes", tmp_path / "repo"
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "forge"))
    chat = _Chat()
    assert "none ·" in cpipe._start(chat, "--rules")
    assert "updating them" in cpipe._start(chat, "—rules commit and push after each commit, only in this project")
    assert "cpipe/rules.md" in chat.sent[0] and chat.sent[0].endswith("only in this project")
    (root / "cpipe").mkdir(parents=True)
    (root / "cpipe" / "rules.md").write_text("- keep commits small\n")
    (root / "profiles" / "forge" / "cpipe").mkdir(parents=True)
    (root / "profiles" / "forge" / "cpipe" / "rules.md").write_text("- run the linter\n")
    (repo / ".git" / "cpipe").mkdir(parents=True)
    (repo / ".git" / "cpipe" / "rules.md").write_text("- commit and push after each commit\n")
    assert "1. keep commits small" in cpipe._start(chat, "--rules")
    run = repo / ".git" / "worktrees" / "fix" / "cpipe"
    run.mkdir(parents=True)
    monkeypatch.setenv("CPIPE_RUN", str(run))
    for step in ("", "1"):
        monkeypatch.setenv("CPIPE_STEP", step)
        pinned = cpipe._pinned({})
        assert pinned.index("keep commits small (everywhere)") < pinned.index("run the linter (this profile)") \
            < pinned.index("commit and push after each commit (this project)")


def test_a_run_that_skips_registration_is_still_pinned_and_watched(tmp_path, monkeypatch):
    import sys, types
    sys.modules.setdefault("gateway", types.ModuleType("gateway"))
    sys.modules["gateway.session_context"] = types.SimpleNamespace(
        get_session_env=lambda name: "chat1" if name == "HERMES_SESSION_ID" else None)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("CPIPE_RUN", raising=False)
    monkeypatch.delenv("CPIPE_STEP", raising=False)
    monkeypatch.delenv("CPIPE_LANE", raising=False)
    monkeypatch.setattr(cpipe, "_waiting", lambda _: False)
    chat = _Chat()
    cpipe._start(chat, "fix the clock audit")
    assert "cpipe.py active <run dir>" in cpipe._pinned({"session_id": "chat1"})
    assert cpipe._pinned({"session_id": "other"}) == ""
    history = [{"role": "user", "content": chat.sent[0]}]
    cpipe._watch(chat, session_id="chat1", user_message="x", assistant_response="Both changes are merged.",
                 conversation_history=history)
    assert "no run folder is registered" in chat.sent[-1]
    run = tmp_path / "run"
    run.mkdir()
    (tmp_path / "cpipe" / "active" / "chat1.json").write_text(cpipe.json.dumps({"run_dir": str(run)}))
    assert "Run folder: " in cpipe._pinned({"session_id": "chat1"})


def test_stop_ends_the_run_at_once(tmp_path, monkeypatch):
    class Ctx:
        text = None
        def inject_message(self, text, **_):
            self.text = text
            return True
    import sys, types
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("CPIPE_RUN", raising=False)
    sys.modules.setdefault("gateway", types.ModuleType("gateway"))
    sys.modules["gateway.session_context"] = types.SimpleNamespace(
        get_session_env=lambda name: "s1" if name == "HERMES_SESSION_ID" else None)
    active = tmp_path / "cpipe" / "active"
    active.mkdir(parents=True)
    (active / "s1.json").write_text(json.dumps({"run_dir": str(tmp_path)}) + "\n")
    (active / "s1.watch").write_text("1\n")
    ctx = Ctx()
    assert cpipe._start(ctx, "stop").startswith("**cpipe** · run stopped")
    assert not list(active.iterdir()) and ctx.text == cpipe.STOP
    start = {"role": "user", "content": cpipe.START.format(skill="s", root="r") + "fix it"}
    assert cpipe._steering("now the modal", [start], session_id="s1") is None  # closed: plain work again
    ctx.text = None
    assert cpipe._start(ctx, "stop") == cpipe.NOT_RUNNING and ctx.text is None
    assert "not to use cpipe" in cpipe.STEERING  # a plain "don't use cpipe" ends the run too


def test_each_mode_is_its_own_command():
    class Ctx:
        commands = {}
        def register_skill(self, *_): pass
        def register_command(self, name, handler, **_):
            self.commands[name] = handler
        def inject_message(self, text, **_):
            self.text = text
            return True
    import sys, types
    sys.modules.setdefault("gateway", types.ModuleType("gateway"))
    sys.modules["gateway.session_context"] = types.SimpleNamespace(get_session_env=lambda _: None)
    ctx = Ctx()
    cpipe.register(ctx)
    assert {f"cpipe-{name}" for name in cpipe.COMMANDS} | {"cpipe"} == set(ctx.commands)
    assert "Spec" in ctx.commands["cpipe-spec"]("fix it") and ctx.text.endswith("--spec fix it")
    assert "Lanes" in ctx.commands["cpipe-parallel"]("fix #1 and #2")
    assert ctx.commands["cpipe-continue"]().startswith("**cpipe** · resuming")
    assert ctx.commands["cpipe-stop"]() == cpipe.NOT_RUNNING
    assert ctx.commands["cpipe-spec"]() == cpipe.USAGE
