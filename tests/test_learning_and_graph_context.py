"""Outcome learning and code-graph context use real Git/SQLite, no model calls."""
import json
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from cpipe import code_context, engines, learning
from cpipe.round import build_prompt, PLAN
from test_stage_commands import existing, git
from test_stage_gates import frozen_runner


def test_learning_is_repository_scoped_idempotent_and_bounded(existing, tmp_path):
    assert learning.record(existing, "first", ["review", "untrusted-text"]) is None
    learning.record(existing, "first", ["review"])
    assert learning.recalled(existing) == [{"pattern": "review", "observed": 1, "recovered": 0,
                                           "text": learning.PATTERNS["review"][1], "structural_followup": None}]
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    assert learning.recalled(other) == []
    for key in learning.PATTERNS:
        learning.record(existing, "all", [key])
    assert len(learning.recalled(existing, 100)) == 5


def test_worktrees_share_lessons_without_sharing_other_repositories(existing, tmp_path):
    other = tmp_path / "linked-worktree"
    git(existing, "worktree", "add", "--detach", str(other), "HEAD")
    learning.record(existing, "first", ["checks"], verified=True)
    assert learning.repository_key(other) == learning.repository_key(existing)
    assert learning.recalled(other)[0]["recovered"] == 1


def test_recurring_verified_recovery_proposes_structure_without_rewriting_files(existing):
    learning.record(existing, "first", ["review"], verified=True)
    learning.record(existing, "first", ["review"], verified=True)
    assert learning.recalled(existing)[0]["structural_followup"] is None
    learning.record(existing, "second", ["review"], verified=True)
    lesson = learning.recalled(existing)[0]
    assert lesson["observed"] == lesson["recovered"] == 2
    assert "test, lint or canonical helper" in lesson["structural_followup"]
    assert git(existing, "status", "--porcelain") == ""
    learning.record(existing, "second", ["review"])
    assert learning.recalled(existing)[0]["recovered"] == 1


def test_learning_store_contains_no_source_prompts_or_raw_findings(existing, hermes_home):
    learning.record(existing, "first", ["review", "private secret finding"])
    db = sqlite3.connect(hermes_home / "state" / "cpipe-learning.sqlite")
    try:
        rows = db.execute("SELECT * FROM observations").fetchall()
    finally:
        db.close()
    assert len(rows) == 1
    assert rows[0][2:] == ("review", 0)
    assert str(existing) not in str(rows)
    assert "private secret finding" not in str(rows)


def test_learning_failure_is_advisory_not_delivery_failure(existing, monkeypatch):
    monkeypatch.setattr(learning, "_connect", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    assert "unavailable" in (learning.record(existing, "first", ["checks"]) or "")
    assert learning.context(existing) == ""


def test_failure_then_verified_recovery_is_reused_in_next_prompt(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    monkeypatch.setattr(r, "qa", lambda: (True, "QA passed"))
    monkeypatch.setattr(r, "security", lambda: (True, "Security passed"))
    monkeypatch.setattr(r, "review", lambda: (False, "REV-001: missing regression"))
    assert r.verify("", "")[0] == "fix"
    assert learning.recalled(existing)[0]["recovered"] == 0
    monkeypatch.setattr(r, "review", lambda: (True, "Review passed"))
    assert r.verify("", "")[0] == "ok"
    assert learning.recalled(existing)[0]["recovered"] == 1
    prompt = build_prompt(r.task, existing, r.state, [])
    assert "Repository outcome lessons" in prompt
    assert "verified recovery in 1" in prompt
    assert "REV-001: missing regression" not in prompt
    assert r.state["verification_receipt"] == r.gate_key()


def test_changed_revision_during_verification_cannot_confirm_learning(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    r.remember(["checks"])
    monkeypatch.setattr(r, "qa", lambda: (True, "QA passed"))
    monkeypatch.setattr(r, "security", lambda: (True, "Security passed"))
    def review():
        (existing / "code.py").write_text("changed while verifying\n")
        return True, "Review passed"
    monkeypatch.setattr(r, "review", review)
    assert r.verify("", "")[0] == "blocked"
    assert learning.recalled(existing)[0]["recovered"] == 0


def test_unavailable_required_gate_cannot_confirm_learning(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    r.remember(["qa"])
    monkeypatch.setattr(r, "qa", lambda: (None, "QA unavailable"))
    monkeypatch.setattr(r, "security", lambda: (True, "Security passed"))
    monkeypatch.setattr(r, "review", lambda: (True, "Review passed"))
    assert r.verify("", "")[0] == "fix"
    assert "verified_head" not in r.state
    assert learning.recalled(existing)[0]["recovered"] == 0


@pytest.mark.parametrize("result", ["ok", "fail", "pending"])
def test_ci_recovery_requires_green_remote_ci_not_only_local_verification(existing, tmp_path, monkeypatch, result):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    r.remember(["ci"])
    monkeypatch.setattr(r, "qa", lambda: (True, "QA passed"))
    monkeypatch.setattr(r, "security", lambda: (True, "Security passed"))
    monkeypatch.setattr(r, "review", lambda: (True, "Review passed"))
    assert r.verify("", "")[0] == "ok"
    assert learning.recalled(existing)[0]["recovered"] == 0
    def remote_ci(ws, state, minutes):
        if result == "ok":
            state["ci_check_evidence"] = {"head": state["verification_receipt"]["head"], "count": 1}
        return result, "remote result"
    monkeypatch.setattr("cpipe.round.pr.ci", remote_ci)
    r.ci("", "")
    assert learning.recalled(existing)[0]["recovered"] == int(result == "ok")


@pytest.mark.parametrize("surface", ["missing-pr", "missing-workflows", "green-ci"])
def test_actual_ci_requires_remote_checks_and_discards_stale_evidence(existing, tmp_path, monkeypatch, surface):
    from cpipe import pr
    r = frozen_runner(tmp_path, monkeypatch, existing)
    r.remember(["ci"])
    key = r.gate_key()
    r.state["verification_receipt"] = key
    r.state["ci_check_evidence"] = {"head": key["head"], "count": 1}  # Old evidence cannot prove a new CI run.
    if surface != "missing-pr":
        r.state["pr_url"] = "https://example.invalid/pull/1"
    if surface != "missing-workflows":
        (existing / ".github" / "workflows").mkdir(parents=True)
    def gh(ws, *args):
        data = {"headRefOid": key["head"]} if args[:2] == ("pr", "view") else [{"bucket": "pass"}]
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
    monkeypatch.setattr(pr, "_gh", gh)
    assert r.ci("", "")[0] == "ok"
    assert learning.recalled(existing)[0]["recovered"] == int(surface == "green-ci")
    if surface != "green-ci":
        assert "ci_check_evidence" not in r.state



def test_content_scope_has_no_learning_or_code_graph_queries(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    r.state["size"] = "content"
    r.remember(["review"])
    assert learning.recalled(existing) == []
    monkeypatch.setattr(code_context, "context", lambda *a: pytest.fail("content called graph tool"))
    assert "Code graph" not in build_prompt(r.task, existing, r.state, [])


def graph_index(existing):
    index = existing / "graphify-out" / "graph.json"
    index.parent.mkdir()
    index.write_text(json.dumps({"directed": True, "multigraph": False, "graph": {},
                                "nodes": [{"id": "one", "label": "Widget", "source_file": "code.py"},
                                          {"id": "two", "label": "WidgetTest", "source_file": "tests/test_code.py"}],
                                "links": [{"source": "two", "target": "one", "relation": "tests", "weight": 1}]}))
    return index


def test_graph_context_bounded_cached_and_invalidated_by_revision(existing, monkeypatch):
    graph_index(existing)
    git(existing, "add", ".")
    git(existing, "commit", "-qm", "chore: graph fixture")
    original = subprocess.run
    calls = []
    def invoke(command, **kwargs):
        if command[0] != "graphify":
            return original(command, **kwargs)
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="Widget calls helper\n" * 1000)
    monkeypatch.setattr(shutil, "which", lambda name: "/tool/" + name)
    monkeypatch.setattr(subprocess, "run", invoke)
    state = {}
    first = code_context.context(existing, "Widget", state)
    assert "index freshness is not certified" in first
    assert len(first) < 6500
    assert first == code_context.context(existing, "Widget", state)
    assert len(calls) == 1
    assert "--budget" in calls[0]
    (existing / "code.py").write_text("value = 3\n")
    git(existing, "commit", "-qam", "fix: widget")
    code_context.context(existing, "Widget", state)
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["timeout", "error", "empty"])
def test_graph_failure_never_becomes_gate_evidence(existing, monkeypatch, failure):
    graph_index(existing)
    original = subprocess.run
    def invoke(command, **kwargs):
        if command[0] != "graphify":
            return original(command, **kwargs)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 15)
        return SimpleNamespace(returncode=1 if failure == "error" else 0, stdout="")
    monkeypatch.setattr(shutil, "which", lambda name: "/tool/" + name)
    monkeypatch.setattr(subprocess, "run", invoke)
    state = {}
    assert "no graph-derived verification" in code_context.context(existing, "Widget", state)
    assert state["code_graph"]["status"] == "unavailable"
    assert "verification_receipt" not in state


def test_codegraph_fallback_uses_existing_index(existing, monkeypatch):
    (existing / ".codegraph").mkdir()
    (existing / ".codegraph" / "codegraph.db").touch()
    original = subprocess.run
    calls = []
    def invoke(command, **kwargs):
        if command[0] != "codegraph":
            return original(command, **kwargs)
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="code.py -> test_code.py")
    monkeypatch.setattr(shutil, "which", lambda name: "/tool/" + name)
    monkeypatch.setattr(subprocess, "run", invoke)
    state = {}
    assert "code.py -> test_code.py" in code_context.context(existing, "Widget", state)
    assert state["code_graph"]["provider"] == "codegraph"
    assert calls[0][:2] == ["codegraph", "explore"]


@pytest.mark.skipif(not shutil.which("graphify"), reason="optional local graphify executable")
def test_real_graph_query_supplies_relationships_without_model_calls(existing):
    graph_index(existing)
    state = {}
    result = code_context.context(existing, "Widget", state)
    assert state["code_graph"]["status"] == "available"
    assert "WidgetTest" in result
    assert "Widget" in result


def test_reviewer_receives_graph_context(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    monkeypatch.setattr(code_context, "context", lambda *a: "CODE_GRAPH_RELATION: Widget -> WidgetTest")
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    seen = []
    def review(*args, **kw):
        seen.append(args[4])
        return engines.EngineResult("approve")
    monkeypatch.setattr(r, "call", review)
    assert r.review()[0] is True
    assert "CODE_GRAPH_RELATION: Widget -> WidgetTest" in seen[0]


def test_plan_explicitly_checks_throughput_and_data_shape():
    assert "throughput checkpoint" in PLAN
    assert "shared mutable state" in PLAN
    assert "data shape" in PLAN
    assert "one coupled change stays with one owner" in PLAN
