"""Profile handoffs and fast paths, using only isolated test state and scripted engines."""
import io
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("hermes_cli.kanban_db", reason="needs a Hermes Agent checkout")

from cpipe import engines, graph, report, settings
from cpipe.engines import hermes
from cpipe.round import Round, feedback


def config():
    return settings.load({
        "default_engine": "builder", "fallback_engine": None,
        "engines": {name: {"kind": "hermes", "profile": profile} for name, profile in
                    [("planner", "archon"), ("builder", "forge"), ("reviewer", "sentry"), ("qa", "sentinel")]},
        "roles": {"plan": "planner", "build": "builder", "review": "reviewer", "qa": "qa"},
    })


def runner(tmp_path, monkeypatch, cfg=None):
    task = SimpleNamespace(title="Change copy", body="Use the requested wording", branch_name="feat/copy", model_override=None)
    monkeypatch.setattr("cpipe.round.hermes_api.get_task", lambda *a: task)
    monkeypatch.setattr("cpipe.round.workspace_prep.project_of", lambda *a: "app")
    monkeypatch.setattr("cpipe.round.workspace_prep.project_profile", lambda *a: {"base_branch": "main"})
    monkeypatch.setattr("cpipe.round.workspace_prep.prepare", lambda *a: [])
    monkeypatch.setattr("cpipe.round.workspace_prep.lessons", lambda *a: [])
    return Round("test", tmp_path, None, "builder", cfg or config())


def test_new_install_needs_only_hermes():
    cfg = settings.load({})
    assert cfg["default_engine"] == "hermes"
    assert cfg["fallback_engine"] is None
    assert cfg["review_engine"] == "hermes"


def test_explicit_legacy_engine_config_is_preserved():
    cfg = settings.load({"engines": {"codex": {"bin": "codex"}}})
    assert cfg["default_engine"] == "codex"


@pytest.mark.parametrize("roles", [{"plan": "missing"}, {"unknown": "builder"}, [], {"build": None}])
def test_invalid_roles_fail_early(roles):
    raw = config()
    raw["roles"] = roles
    with pytest.raises(ValueError, match="roles"):
        settings.load(raw)


def test_profile_handoff_keeps_builder_warm_and_planner_separate(tmp_path, monkeypatch):
    r = runner(tmp_path, monkeypatch)
    calls = []

    def run(cfg, prompt, cwd, **kw):
        calls.append((cfg["profile"], kw["session"], prompt))
        return engines.EngineResult("done", session=cfg["profile"] + "-session")

    monkeypatch.setattr(hermes, "run", run)
    r.state["plan"] = "Only adjust the requested copy"
    r.turn("plan", "Plan it")
    r.turn("tests", "Write tests")
    r.turn("build", "Implement")
    r.turn("build", "Fix REV-001")
    assert [(p, s) for p, s, _ in calls] == [
        ("archon", None), ("forge", None), ("forge", "forge-session"), ("forge", "forge-session")]
    assert "Only adjust the requested copy" in calls[1][2]
    assert r.state["sessions"]["planner"] == "archon-session"
    assert r.state["sessions"]["builder"] == "forge-session"


@pytest.mark.parametrize("status", ["done", "failed", "rate_limited"])
def test_turn_without_usage_records_call_and_session(tmp_path, monkeypatch, status):
    r = runner(tmp_path, monkeypatch)
    monkeypatch.setattr(hermes, "run", lambda *a, **kw:
                        engines.EngineResult(status, session="builder-session", tokens=None))
    result = r.turn("build", "Implement", fallback=False)
    assert result.status == status
    assert r.state["usage"] == {}
    assert r.state["sessions"]["builder"] == "builder-session"
    assert r.state["agent_calls"][0]["tokens"] is None
    monkeypatch.setattr(report, "state_of", lambda *a: r.state)
    monkeypatch.setattr("cpipe.runtime.hermes_api.run_count", lambda *a: 1)
    row = report.row(None, SimpleNamespace(id="test", title="Task", status="review", assignee="cp:builder"))
    assert row["calls_without_usage"] == 1


def test_rate_limit_without_usage_can_reach_configured_fallback(tmp_path, monkeypatch):
    cfg = config()
    cfg["fallback_engine"] = "planner"
    r = runner(tmp_path, monkeypatch, cfg)
    calls = []
    def run(ec, *a, **kw):
        calls.append(ec["profile"])
        if ec["profile"] == "forge":
            return engines.EngineResult("rate_limited", tokens=None)
        return engines.EngineResult("done", session="fallback-session", tokens={"input_tokens": 10})
    monkeypatch.setattr(hermes, "run", run)
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    assert r.turn("build", "Implement").status == "done"
    assert calls == ["forge", "archon"]
    assert r.state["usage"] == {"input_tokens": 10}
    assert r.state["agent_calls"][0]["tokens"] is None
    assert r.state["session"] == "fallback-session"


@pytest.mark.parametrize("gate", ["review", "qa"])
def test_required_gate_failure_checkpoints_the_full_round(tmp_path, monkeypatch, gate):
    from cpipe.round import load_state
    r = runner(tmp_path, monkeypatch)
    r.state.update(node="verify", approved=True)
    monkeypatch.setattr("cpipe.round.feedback", lambda *a: "")
    monkeypatch.setattr(r, "qa", lambda: (True, "QA: passed"))
    monkeypatch.setattr(r, "review", lambda: (True, "Review: approved"))
    monkeypatch.setattr(r, "security", lambda: (True, "Security checks: passed"))
    reason = f"{gate} unavailable: missing executable"
    monkeypatch.setattr(r, gate, lambda: (None, reason))
    monkeypatch.setattr(r, "pr", lambda *a: pytest.fail("a missing required gate must never publish"))
    out = r.run()
    assert out["status"] == "blocked"
    assert out["summary"] == reason
    assert r.state["node"] == "stuck"
    assert "verified_head" not in r.state
    saved = load_state(tmp_path)
    assert saved is not None
    assert saved["node"] == "stuck" and saved["why"] == reason
    assert saved["timeline"][-1]["outcome"] == "blocked"


def test_qa_never_resumes_the_builder(tmp_path, monkeypatch):
    r = runner(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(hermes, "run", lambda cfg, prompt, cwd, **kw:
                        calls.append((cfg["profile"], kw["session"])) or
                        engines.EngineResult("done", session=cfg["profile"] + "-session"))
    r.turn("build", "Implement")
    r.turn("qa", "Inspect", fallback=False)
    r.turn("build", "Continue")
    assert calls == [("forge", None), ("sentinel", None), ("forge", "forge-session")]


def test_review_uses_its_role_and_only_reviews_delta_on_rework(tmp_path, monkeypatch):
    r = runner(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr("cpipe.round.engines.available", lambda *a: (True, ""))
    monkeypatch.setattr("cpipe.round.head", lambda *a: "new-head")
    monkeypatch.setattr(r, "base_sha", lambda: "base-head")
    monkeypatch.setattr(hermes, "run", lambda cfg, prompt, cwd, **kw:
                        calls.append((cfg["profile"], prompt, kw)) or
                        engines.EngineResult("approve", session="review-session"))
    assert r.review()[0] is True
    r.state["review_head"] = "old-head"
    monkeypatch.setattr("cpipe.round.subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=0))
    r.state["findings"] = ["REV-001: wrong label"]
    assert r.review()[0] is True
    assert calls[0][0] == "sentry"
    assert calls[0][2]["session"] is None
    assert calls[1][2]["session"] == "review-session"
    assert "old-head..new-head" in calls[1][1]
    assert "REV-001" in calls[1][1]
    assert r.state["review_head"] == "new-head"


@pytest.mark.parametrize("size,outcome,next_node", [("large", "ok", "plan"), ("small", "approved", "tests"),
                                                    ("content", "content", "build")])
def test_size_routes_without_removing_code_test_gate(tmp_path, monkeypatch, size, outcome, next_node):
    r = runner(tmp_path, monkeypatch)
    r.state["size"] = size
    actual, prompt = r.setup("", "")
    assert actual == outcome
    assert graph.next_node("setup", actual) == next_node
    if size != "large":
        assert r.state["approved"] is True
    if size == "content":
        assert "Do not add tests" in prompt


def test_size_directive_is_not_user_feedback(tmp_path, monkeypatch):
    r = runner(tmp_path, monkeypatch)
    comments = [SimpleNamespace(id=1, author="cpipe:size", body="small")]
    monkeypatch.setattr("cpipe.round.hermes_api.comments_after", lambda *a: comments)
    assert feedback(None, "test", r.state) == ""
    assert r.state["size"] == "small"


def test_bad_size_directive_cannot_skip_plan(tmp_path, monkeypatch):
    r = runner(tmp_path, monkeypatch)
    monkeypatch.setattr("cpipe.round.hermes_api.comments_after", lambda *a:
                        [SimpleNamespace(id=1, author="cpipe:size", body="invalid")])
    feedback(None, "test", r.state)
    assert r.state.get("size", "large") == "large"


def test_content_review_stays_within_scope(tmp_path, monkeypatch):
    r = runner(tmp_path, monkeypatch)
    r.state["size"] = "content"
    monkeypatch.setattr("cpipe.round.engines.available", lambda *a: (True, ""))
    monkeypatch.setattr("cpipe.round.head", lambda *a: "head")
    monkeypatch.setattr(r, "base_sha", lambda: "base")
    prompts = []
    monkeypatch.setattr(hermes, "run", lambda cfg, prompt, cwd, **kw:
                        prompts.append(prompt) or engines.EngineResult("approve"))
    r.review()
    assert "Do not require new tests" in prompts[0]


def test_hermes_inherits_profile_model_unless_explicitly_overridden(tmp_path):
    argv = hermes.command({"profile": "forge"}, "task", tmp_path, None)
    assert argv[2] == "forge"
    assert "--model" not in argv
    explicit = hermes.command({"profile": "forge", "model": "explicit"}, "task", tmp_path, "session")
    assert explicit[explicit.index("--model") + 1] == "explicit"
    assert explicit[-2:] == ["--resume", "session"]


@pytest.mark.parametrize("exit_code,error,status", [(1, "rate limit exceeded", "rate_limited"),
                                                   (1, "invalid api key", "unavailable"), (1, "boom", "failed")])
def test_hermes_failed_exit_cannot_claim_success(tmp_path, monkeypatch, exit_code, error, status):
    ev = {"type": "result", "exit_code": exit_code, "error": error, "text":
          '```json\n{"status":"done","summary":"not really"}\n```', "session_id": "s"}
    proc = SimpleNamespace(stdout=io.StringIO(json.dumps(ev) + "\n"), returncode=exit_code, wait=lambda: exit_code)
    monkeypatch.setattr(hermes.subprocess, "Popen", lambda *a, **kw: proc)
    assert hermes.run({}, "task", tmp_path).status == status


def test_hermes_success_records_tokens(tmp_path, monkeypatch):
    ev = {"type": "result", "exit_code": 0, "text": '```json\n{"status":"done"}\n```',
          "session_id": "s", "tokens": {"input": 100, "cache_read": 40, "output": 20}}
    proc = SimpleNamespace(stdout=io.StringIO(json.dumps(ev) + "\n"), returncode=0, wait=lambda: 0)
    monkeypatch.setattr(hermes.subprocess, "Popen", lambda *a, **kw: proc)
    result = hermes.run({}, "task", tmp_path)
    assert result.status == "done"
    assert result.tokens == {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20}


def test_hermes_worker_prompt_does_not_recursively_coordinate(tmp_path, monkeypatch):
    seen = []
    proc = SimpleNamespace(stdout=io.StringIO(), returncode=1, wait=lambda: 1)
    def start(argv, **kwargs):
        seen.append(argv[argv.index("-q") + 1])
        return proc
    monkeypatch.setattr(hermes.subprocess, "Popen", start)
    hermes.run({}, "Review the copy", tmp_path, readonly=True)
    assert "Do not call delivery_*, cpipe_* or kanban_* tools" in seen[0]
    assert "invoke external coding CLIs" in seen[0]
    assert "Do not modify any file." in seen[0]
    assert "Review the copy" in seen[0]


def test_report_separates_parallel_agent_work_from_elapsed_time(monkeypatch):
    st = {"timeline": [{"node": "verify", "start": 0, "end": 60}], "agent_calls": [
        {"node": "review", "engine": "reviewer", "profile": "sentry", "seconds": 50},
        {"node": "qa", "engine": "qa", "profile": "sentinel", "seconds": 50}],
        "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20}}
    monkeypatch.setattr(report, "state_of", lambda *a: st)
    monkeypatch.setattr("cpipe.runtime.hermes_api.run_count", lambda *a: 1)
    row = report.row(None, SimpleNamespace(id="test", title="Task", status="review", assignee="cp:builder"))
    assert row["minutes"] == 1
    assert row["agent_seconds"] == 100
    assert row["agent_calls"] == 2
    assert row["cached_input_tokens"] == 40
    assert row["tokens"] == 120
    assert row["nodes"]["review"]["seconds"] == 50


def test_content_prompt_and_qa_keep_the_requested_scope(tmp_path, monkeypatch):
    from cpipe.round import build_prompt
    r = runner(tmp_path, monkeypatch)
    r.state["size"] = "content"
    prompt = build_prompt(r.task, r.ws, r.state, [])
    assert "do not add tests or change application logic" in prompt
    assert "Test first:" not in prompt
    monkeypatch.setattr("cpipe.round.test_env.start", lambda *a: pytest.fail("content QA must not start an app"))
    assert r.qa() == (None, "QA: not needed for content-only scope")


def test_explicit_reviewer_unavailable_stops_without_builder_fallback(tmp_path, monkeypatch):
    r = runner(tmp_path, monkeypatch)
    monkeypatch.setattr(engines, "available", lambda *a: (False, "missing"))
    monkeypatch.setattr(hermes, "run", lambda *a, **kw: pytest.fail("do not replace an explicit reviewer with the builder"))
    monkeypatch.setattr(r, "qa", lambda: (True, "QA: passed"))
    monkeypatch.setattr(r, "security", lambda: (True, "Security checks: passed"))
    out, detail = r.verify("build", "")
    assert out == "blocked"
    assert "configured role reviewer is unavailable" in detail
    assert "verified_head" not in r.state


def test_required_qa_cannot_silently_pass(tmp_path, monkeypatch):
    r = runner(tmp_path, monkeypatch)
    monkeypatch.setattr(r, "qa", lambda: (None, "QA: app could not start"))
    monkeypatch.setattr(r, "review", lambda: (True, "Review: approved"))
    monkeypatch.setattr(r, "security", lambda: (True, "Security checks: passed"))
    assert r.verify("build", "") == ("blocked", "QA: app could not start")
    assert "verified_head" not in r.state


@pytest.mark.parametrize("cold", [True, False])
def test_cold_or_rewritten_review_uses_full_range_and_prior_findings(tmp_path, monkeypatch, cold):
    r = runner(tmp_path, monkeypatch)
    r.state.update(review_engine="reviewer", review_session=None if cold else "sentry-session",
                   review_owner=["reviewer", hermes.__name__, "sentry"], review_head="old-head",
                   findings=["REV-001: fix the copy"])
    monkeypatch.setattr("cpipe.round.head", lambda *a: "new-head")
    monkeypatch.setattr("cpipe.round.subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=1))
    monkeypatch.setattr(engines, "available", lambda *a: (True, "ok"))
    monkeypatch.setattr(r, "base_sha", lambda: "base-head")
    monkeypatch.setattr(r, "tests_changed", lambda: [])
    seen = []
    monkeypatch.setattr(hermes, "run", lambda cfg, prompt, cwd, **kw: seen.append(prompt) or
                        engines.EngineResult("approve", session="sentry-session"))
    assert r.review()[0] is True
    assert "git diff base-head...new-head" in seen[0]
    assert "git diff old-head..new-head" not in seen[0]
    assert "REV-001: fix the copy" in seen[0]
