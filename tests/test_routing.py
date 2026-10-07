"""Consent, persistence and stage ownership; never execute a live delivery."""
import json
import re
from copy import deepcopy
from types import SimpleNamespace

import pytest

pytest.importorskip("hermes_cli.kanban_db")
from cpipe import engines, intents, native, routing, settings, tools
from cpipe.engines import hermes
from cpipe.round import Round
from cpipe.runtime import hermes_api as h


@pytest.fixture
def flow(hermes_home, monkeypatch):
    for profile in ("planner", "builder", "reviewer", "qa"):
        file = hermes_home / "profiles" / profile / "config.yaml"
        file.parent.mkdir(parents=True)
        file.write_text("model:\n  default: test-model\n")
    cfg = settings.load({"default_engine": "codex", "fallback_engine": "claude",
        "engines": {**{name: {"kind": "hermes", "profile": name} for name in ("planner", "builder", "reviewer", "qa")},
                    "codex": {"kind": "codex", "bin": "codex"}, "claude": {"kind": "claude", "bin": "claude"}},
        "roles": {"plan": "planner", "build": "builder", "review": "reviewer", "qa": "qa"}})
    monkeypatch.setattr(settings, "load", lambda *a: cfg)
    monkeypatch.setattr(intents, "_session_key", lambda: "chat-a")
    monkeypatch.setattr(native, "_session", lambda explicit: explicit or "chat-a")
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    monkeypatch.setattr(intents, "_active_delivery", lambda *a: None)
    calls = []
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", lambda args: calls.append(args) or {"task_id": "test-only"})
    class Context:
        def inject_message(self, prompt, **kwargs):
            self.prompt = prompt
            return True
    ctx = Context()
    intents.dispatch(ctx, "cp-plan", "--size small app: Save submission dates")
    args = {"token": re.search(r"receipt: ([\w-]+)", ctx.prompt)[1], "operation": "plan",
            "arguments": {"request": "Save submission dates", "project": "app", "size": "small", "engine": "codex"}}
    return args, cfg, calls


def answer(pending, value, session="chat-a", **extra):
    form = pending["clarify"]
    return native.after_tool(session_id=session, tool_name="clarify", args=form,
        result={"responses": [{"question": form["questions"][0]["question"], "user_response": value}], **extra})


def start(flow, mode, engine=None):
    args, cfg, calls = flow
    pending = json.loads(intents.execute(args))
    assert pending["status"] == "needs_routing" and not calls
    assert len(pending["clarify"]["questions"][0]["choices"]) == 3
    pending = answer(pending, mode)
    if engine:
        pending = answer(pending, engine)
    assert pending["phase"] == "confirm" and not calls
    return pending


@pytest.mark.parametrize("mode,engine,expected", [
    ("Single Hermes profile (default or another)", "hermes", "single"),
    ("Single Hermes profile (default or another)", "builder", "single"),
    ("External coding CLI", "codex", "external"),
    ("Specialist bots/profiles", None, "specialists"),
])
def test_three_routes_require_confirmation_and_do_not_change_defaults(flow, mode, engine, expected):
    args, cfg, calls = flow
    before = deepcopy(cfg)
    pending = start(flow, mode, engine)
    assert "Automatic fallback is disabled" in pending["clarify"]["questions"][0]["question"]
    out = answer(pending, "Start with this route")
    assert out["task_id"] == "test-only" and len(calls) == 1
    route = calls[0]["routing"]
    assert route["mode"] == expected
    effective = routing.effective_config(cfg, route)
    assert effective["fallback_engine"] is None
    if engine:
        assert set(effective["roles"].values()) == {engine}
        assert "independent" not in route["review_kind"]
    else:
        assert effective["roles"]["review"] == "reviewer"
    assert cfg == before
    assert answer(pending, "Start with this route") is None
    assert json.loads(intents.execute(args))["task_id"] == "test-only"


def test_routing_cannot_be_inferred_from_caller_arguments_or_replaced_on_retry(flow):
    args, _, calls = flow
    args["arguments"]["routing"] = {"mode": "external", "engine": "codex"}
    pending = json.loads(intents.execute(args))
    assert pending["phase"] == "mode" and not calls
    assert "engine" not in pending["arguments"] and "routing" not in pending["arguments"]
    assert json.loads(intents.execute({**args, "arguments": {"request": "different task"}})) == pending


@pytest.mark.parametrize("extra", [{"timed_out": True}, {"notice": "not delivered"}])
def test_timeout_does_not_start_work(flow, extra):
    pending = json.loads(intents.execute(flow[0]))
    assert answer(pending, "External coding CLI", **extra) is None
    assert not flow[2]


def test_wrong_session_and_changed_form_are_rejected(flow):
    pending = json.loads(intents.execute(flow[0]))
    assert answer(pending, "External coding CLI", session="chat-b") is None
    pending["clarify"]["questions"][0]["question"] += " changed"
    assert answer(pending, "External coding CLI") is None
    assert not flow[2]


def test_cancel_does_not_submit(flow):
    pending = start(flow, "Single Hermes profile (default or another)", "hermes")
    assert answer(pending, "Cancel")["status"] == "cancelled"
    assert not flow[2]


def test_unavailable_external_route_retains_mode_form(flow, monkeypatch):
    monkeypatch.setattr(engines, "available", lambda name, ec: (engines.kind(name, ec) == "hermes", "missing CLI"))
    pending = json.loads(intents.execute(flow[0]))
    out = answer(pending, "External coding CLI")
    assert "No available" in out["error"] and out["clarify"] == pending["clarify"]
    assert not flow[2]
    assert answer(out, "Single Hermes profile (default or another)")["phase"] == "engine"


def test_missing_specialist_config_and_same_profile_are_not_silent_single_mode(flow):
    args, cfg, calls = flow
    cfg["roles"] = {}
    pending = json.loads(intents.execute(args))
    assert "Specialist routing needs" in answer(pending, "Specialist bots/profiles")["error"]
    cfg["roles"] = {role: "builder" for role in routing.ROLES}
    assert "different build and review" in answer(pending, "Specialist bots/profiles")["error"]
    assert not calls


def test_profile_and_cli_choices_never_cross_modes(flow):
    cfg = flow[1]
    assert set(routing.candidates(cfg, "external")) == {"codex", "claude"}
    assert "codex" not in routing.candidates(cfg, "single")
    with pytest.raises(ValueError):
        routing.snapshot(cfg, "single", "codex")


def test_route_unavailable_at_confirmation_does_not_submit(flow, monkeypatch):
    pending = start(flow, "External coding CLI", "codex")
    monkeypatch.setattr(engines, "available", lambda *a: (False, "removed"))
    assert "became unavailable" in answer(pending, "Start with this route")["error"]
    assert not flow[2]


def test_observed_profile_model_is_display_only_without_secret_metadata(flow, hermes_home):
    cfg = flow[1]
    (hermes_home / "config.yaml").write_text("model:\n  default: observed-model\n  api_key: [REDACTED]\n")
    saved = routing.snapshot(cfg, "single", "hermes")
    assert saved["engines"]["hermes"]["observed_model"] == "observed-model"
    assert "observed-model (observed profile default)" in routing.describe(saved)
    assert "model" not in saved["engines"]["hermes"]
    assert "api_key" not in json.dumps(saved)


def test_profile_model_lookup_resolves_base_from_active_named_profile(flow, hermes_home, monkeypatch):
    import cpipe.home
    (hermes_home / "config.yaml").write_text("model:\n  default: default-model\n")
    (hermes_home / "profiles" / "builder" / "config.yaml").write_text("model:\n  default: builder-model\n")
    monkeypatch.setattr(cpipe.home, "root", lambda: hermes_home / "profiles" / "planner")
    assert routing.configured_model({"kind": "hermes", "profile": "default"}) == "default-model"
    assert routing.configured_model({"kind": "hermes", "profile": "builder"}) == "builder-model"


def test_card_and_resumed_round_keep_single_owner_despite_global_overrides(flow, hermes_home, tmp_path, monkeypatch):
    cfg = flow[1]
    cfg["engines"]["builder"]["qa_profile"] = "qa"
    route = routing.snapshot(cfg, "single", "builder")
    conn = h.connect(None)
    try:
        tid = h.create_card(conn, title="Change copy", body="Use requested wording", assignee="cp:builder",
                            repo=str(tmp_path), branch="feat/copy", routing=route)
        assert json.loads(next(c.body for c in h.comments_after(conn, tid) if c.author == "cpipe:routing")) == route
        monkeypatch.setattr("cpipe.round.workspace_prep.project_of", lambda *a: "app")
        monkeypatch.setattr("cpipe.round.workspace_prep.project_profile", lambda *a: {"base_branch": "main"})
        cfg["default_engine"] = "claude"
        cfg["role_overrides"] = {"plan": {"profile": "planner"}, "qa": {"profile": "qa"}}
        r = Round(tid, tmp_path, conn, "builder", cfg)
        calls = []
        def run(ec, *a, **kw):
            calls.append((ec["profile"], kw.get("profile")))
            schema = kw.get("schema")
            status = "approve" if schema == engines.REVIEW_SCHEMA else "pass" if schema == engines.QA_SCHEMA else "done"
            return engines.EngineResult(status, summary="isolated test result", data={})
        monkeypatch.setattr(hermes, "run", run)
        for node in ("plan", "tests", "build", "qa"):
            r.turn(node, "Do the assigned stage")
        assert calls == [("builder", None)] * 4
        assert r.cfg["fallback_engine"] is None
        assert r.cfg["engines"]["builder"].get("qa_profile") is None
        for role in ("discover", "review"):
            name = r.role_engine(role)
            ec = r.engine(name)[2]
            argv = hermes.command(ec, "Do the assigned stage", tmp_path, None)
            assert argv[:3] == ["hermes", "-p", "builder"]
        monkeypatch.setattr(r, "gate_key", lambda: {"head": "test-head"})
        monkeypatch.setattr(r, "clean_revision", lambda: True)
        monkeypatch.setattr(r, "base_sha", lambda: "test-base")
        r.state["size"] = "content"
        assert r.review()[0] is True
        r.state["size"] = "small"
        monkeypatch.setattr("cpipe.round.test_env.start", lambda *a: {"ok": True, "urls": {"web": "http://test.invalid"}})
        assert r.qa()[0] is True
        assert calls[-2:] == [("builder", None)] * 2
        assert r.state["routing"]["review_kind"] == "same-profile review"
    finally:
        conn.close()
