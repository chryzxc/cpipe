"""Drive build confirmation through Hermes' real tool-result hook pipeline."""
import json
import re
from copy import deepcopy

import pytest

from cpipe import engines, intents, native, routing, settings, tools


@pytest.fixture
def bridge(hermes_home, monkeypatch, tmp_path):
    import model_tools
    from tools.registry import registry
    from hermes_cli import plugins
    for profile in ("builder", "reviewer"):
        p = hermes_home / "profiles" / profile / "config.yaml"
        p.parent.mkdir(parents=True)
        p.write_text("model:\n  default: test-model\n")
    cfg = settings.load({"roles": {}})
    monkeypatch.setattr(settings, "load", lambda *a: cfg)
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    monkeypatch.setattr(intents, "_active_delivery", lambda *a: False)
    monkeypatch.setattr(intents, "_session_key", lambda: "chat-key")
    monkeypatch.setattr(native, "_session", lambda explicit: explicit or "chat-a")
    import subprocess
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "--allow-empty", "-qm", "chore: baseline"], check=True)
    calls = []
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", lambda args: calls.append(args) or {"task_id": "test-only"})
    class Context:
        def inject_message(self, message, **kwargs):
            self.message = message
            return True
        def register_hook(self, name, handler):
            hooks[name] = handler
    ctx, hooks = Context(), {}
    native.register(ctx)
    intents.dispatch(ctx, "cp-build", "Build matching cpipe plan")
    receipt = {"token": re.search(r"receipt: ([\w-]+)", ctx.message)[1],
               "operation": "build", "arguments": {"request": "Save submission dates", "repo": str(repo)}}
    pending = json.loads(intents.execute(receipt))
    monkeypatch.setattr(plugins, "has_hook", lambda name: name in hooks)
    monkeypatch.setattr(plugins, "invoke_hook", lambda name, **kwargs: [hooks[name](**kwargs)] if name in hooks else [])
    def clarify(form, values, session="chat-a", **extra):
        rows = [{"question": q["question"], "user_response": value} for q, value in zip(form["questions"], values)]
        original = {"responses": rows, **extra}
        monkeypatch.setattr(registry, "dispatch", lambda *a, **kw: json.dumps(original))
        result = model_tools.handle_function_call("clarify", form, session_id=session, skip_pre_tool_call_hook=True)
        return json.loads(result), original
    return pending, clarify, calls, cfg, receipt


def test_build_missing_roles_continues_in_same_tool_loop_exactly_once(bridge):
    pending, clarify, calls, cfg, receipt = bridge
    original_cfg = deepcopy(cfg)
    out, original = clarify(pending["clarify"], ["Start a small delivery"])
    assert out["responses"] == original["responses"]
    pending = out["cpipe"]
    out, _ = clarify(pending["clarify"], ["Specialist bots/profiles"])
    pending = out["cpipe"]
    assert pending["phase"] == "roles" and not calls
    assert set(pending["candidates"]) >= {"hermes", "profile:builder", "profile:reviewer"}
    owners = ["hermes", "profile:builder", "profile:reviewer", "hermes"]
    out, _ = clarify(pending["clarify"], owners)
    pending = out["cpipe"]
    assert pending["phase"] == "confirm" and not calls
    assert "test-model" in pending["clarify"]["questions"][0]["question"]
    assert clarify(pending["clarify"], ["Start with this route"])[0]["cpipe"]["task_id"] == "test-only"
    assert len(calls) == 1 and cfg == original_cfg
    route = calls[0]["routing"]
    assert route["engine"] == "hermes"  # Existing daemon lane; no YAML mutation needed.
    assert route["engines"][route["roles"]["build"]]["profile"] == "builder"
    assert route["engines"][route["roles"]["review"]]["profile"] == "reviewer"
    assert routing.effective_config(cfg, route)["fallback_engine"] is None
    assert intents.build_start_context("chat-a") is None
    assert "cpipe" not in clarify(pending["clarify"], ["Start with this route"])[0]
    assert len(calls) == 1
    assert json.loads(intents.execute(receipt))["task_id"] == "test-only"


def test_invalid_owners_are_visible_and_can_be_corrected(bridge):
    pending, clarify, calls, _, _ = bridge
    pending = clarify(pending["clarify"], ["Start a small delivery"])[0]["cpipe"]
    pending = clarify(pending["clarify"], ["Specialist bots/profiles"])[0]["cpipe"]
    pending = clarify(pending["clarify"], ["hermes"] * 4)[0]["cpipe"]
    assert "different build and review" in pending["error"] and pending["phase"] == "roles"
    assert not calls
    pending = clarify(pending["clarify"], ["hermes", "profile:builder", "profile:reviewer", "hermes"])[0]["cpipe"]
    assert pending["phase"] == "confirm" and "error" not in pending
    assert clarify(pending["clarify"], ["Cancel"])[0]["cpipe"]["status"] == "cancelled"
    assert not calls


@pytest.mark.parametrize("extra", [{"timed_out": True}, {"notice": "not delivered"}])
def test_undelivered_build_confirmation_is_unchanged(bridge, extra):
    pending, clarify, calls, _, _ = bridge
    out, original = clarify(pending["clarify"], ["Start a small delivery"], **extra)
    assert out == original and not calls


def test_wrong_session_and_unrelated_forms_cannot_advance(bridge):
    pending, clarify, calls, _, _ = bridge
    out, original = clarify(pending["clarify"], ["Start a small delivery"], session="chat-b")
    assert out == original and not calls
    unrelated = {"questions": [{"question": "Continue?", "choices": ["Yes", "No"]}]}
    out, original = clarify(unrelated, ["Yes"])
    assert out == original and not calls

def test_confirmed_specialists_create_one_real_session_card_with_frozen_route(bridge, board, tmp_path, monkeypatch):
    from cpipe.runtime import hermes_api as h
    from tools import kanban_tools, async_delegation
    pending, clarify, calls, cfg, receipt = bridge
    from pathlib import Path
    repo = Path(receipt["arguments"]["repo"])
    monkeypatch.setattr(tools, "resolve_project", lambda *a: ("testapp", repo))
    monkeypatch.setattr(async_delegation, "_current_origin_session_id", lambda: "chat-a")
    monkeypatch.setattr(kanban_tools, "_persisted_session_id", lambda sid: sid)
    monkeypatch.setitem(tools.HANDLERS, "cpipe_plan", tools.plan)
    pending = clarify(pending["clarify"], ["Start a small delivery"])[0]["cpipe"]
    pending = clarify(pending["clarify"], ["Specialist bots/profiles"])[0]["cpipe"]
    pending = clarify(pending["clarify"], ["hermes", "profile:builder", "profile:reviewer", "hermes"])[0]["cpipe"]
    assert not h.list_cards(board)
    result = clarify(pending["clarify"], ["Start with this route"])[0]["cpipe"]
    assert "task_id" in result, result
    task = h.get_task(board, result["task_id"])
    assert task is not None
    assert task.session_id == "chat-a" and task.assignee == "cp:hermes" and task.status == "ready"
    route = json.loads(next(c.body for c in h.comments_after(board, task.id) if c.author == "cpipe:routing"))
    assert route == pending["routing"]
    assert route["engines"][route["roles"]["build"]]["profile"] == "builder"
    assert route["engines"][route["roles"]["review"]]["profile"] == "reviewer"
    assert route["engines"][route["roles"]["build"]]["observed_model"] == "test-model"
    assert "cpipe" not in clarify(pending["clarify"], ["Start with this route"])[0]
    assert json.loads(intents.execute(receipt))["task_id"] == task.id
    assert len(h.list_cards(board)) == 1
