"""Late-entry safety and revision-bound gate reuse; real local Git, no model calls."""
from types import SimpleNamespace

import pytest

from cpipe import engines, graph, settings, workspace_prep
from cpipe.engines import hermes
from cpipe.round import head
from test_profiled_workflow import runner
from test_stage_commands import existing, git

_load = settings.load


def frozen_runner(tmp_path, monkeypatch, repo):
    monkeypatch.setattr(settings, "load", _load)
    r = runner(tmp_path, monkeypatch)
    r.ws = repo
    r.state.update(approved=True, entry="verify", publish=False)
    return r


def test_adoption_setup_does_not_reset_existing_remote_commit(existing, tmp_path, monkeypatch):
    prepare = workspace_prep.prepare
    source = head(existing)
    git(existing, "update-ref", "refs/remotes/origin/main", "main")
    git(existing, "update-ref", "refs/remotes/origin/source", "HEAD")
    r = frozen_runner(tmp_path, monkeypatch, existing)
    r.state.update(adopted=True, adoption={"head": source, "base_head": git(existing, "rev-parse", "main")}, entry="review")
    monkeypatch.setattr(workspace_prep, "prepare", prepare)
    outcome, _ = r.setup("", "")
    assert outcome == "review"
    assert head(existing) == source
    assert (existing / "code.py").read_text() == "value = 2\n"


def test_review_only_cannot_approve_uncommitted_reviewer_edits(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    def mutate(*args, **kw):
        (existing / "code.py").write_text("value = 3\n")
        return engines.EngineResult("approve")
    monkeypatch.setattr(r, "call", mutate)
    assert r.review_stage("", "")[0] == "blocked"
    assert "review_receipt" not in r.state


def test_readiness_requires_frozen_verification_not_only_deterministic_checks(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    r.state.update(verified_head="old-head", ready=True)
    monkeypatch.setattr("cpipe.round.pr.ready", lambda *a: pytest.fail("stale approval cannot ship"))
    assert r.final("", "")[0] == "reverify"
    assert graph.next_node("final", "reverify") == "checks"


def test_readiness_rechecks_gate_configuration(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    key = r.gate_key()
    r.state.update(verified_head=key["head"], verification_receipt=key, checks_receipt=key, review_receipt=key)
    r.cfg["engines"]["reviewer"]["profile"] = "another-reviewer"
    monkeypatch.setattr("cpipe.round.pr.ready", lambda *a: pytest.fail("changed inputs need re-verification"))
    assert r.final("", "")[0] == "reverify"


def test_unchanged_revision_reuses_checks_and_verification_without_model_calls(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    key = r.gate_key()
    r.state.update(checks_receipt=key, verification_receipt=key)
    monkeypatch.setattr("cpipe.round.quality.run_checks", lambda *a, **kw: pytest.fail("valid checks were repeated"))
    monkeypatch.setattr(r, "qa", lambda: pytest.fail("valid QA was repeated"))
    monkeypatch.setattr(r, "review", lambda: pytest.fail("valid review was repeated"))
    assert r.checks("", "")[0] == "ok"
    assert r.verify("", "")[0] == "ok"


def test_ship_prompt_text_does_not_invalidate_review_but_changed_review_scope_does(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    key = r.gate_key()
    r.state["stage_text"] = "create a draft PR for this"
    assert r.gate_key() == key
    r.state["review_scope"] = "Also verify the export behavior discussed above"
    assert r.gate_key() != key


def test_changed_stage_scope_invalidates_receipts_and_forces_full_range(existing, tmp_path, monkeypatch):
    import json
    from cpipe.round import feedback
    r = frozen_runner(tmp_path, monkeypatch, existing)
    calls = []
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    monkeypatch.setattr(hermes, "run", lambda ec, prompt, *a, **kw:
                        calls.append(prompt) or engines.EngineResult("approve", session="review-session"))
    assert r.review()[0] is True
    old = r.state["review_receipt"]
    comment = SimpleNamespace(id=1, author="cpipe:stage", body=json.dumps(
        {"entry": "verify", "text": "Also preserve empty-state behavior"}))
    monkeypatch.setattr("cpipe.round.hermes_api.comments_after", lambda *a: [comment])
    feedback(None, r.task_id, r.state)
    assert r.gate_key() != old
    assert r.review()[0] is True and len(calls) == 2
    assert f"{old['head']}..{old['head']}" not in calls[-1]
    assert f"{old['base']}...{old['head']}" in calls[-1]
    assert "Also preserve empty-state behavior" in calls[-1]


def test_missing_checks_are_not_described_as_passes():
    from cpipe.round import Round
    assert "not a test pass" in Round.check_label("Checks", [])


@pytest.mark.parametrize("checks", [[], [{"name": "test", "bucket": "pending"}],
                                   [{"name": "test", "bucket": "cancel"}],
                                   [{"name": "test", "bucket": "skipping"}]])
def test_incomplete_ci_is_not_a_pass(existing, monkeypatch, checks):
    import json
    from cpipe import pr
    (existing / ".github" / "workflows").mkdir(parents=True)
    times = iter([0, 100])
    monkeypatch.setattr(pr.time, "time", lambda: next(times))
    monkeypatch.setattr(pr.time, "sleep", lambda *a: pytest.fail("timeout must return, not poll"))
    monkeypatch.setattr(pr, "_gh", lambda ws, *args, **kw: SimpleNamespace(
        returncode=0, stderr="", stdout=json.dumps({"headRefOid": head(ws)} if args[1] == "view" else checks)))
    assert pr.ci(existing, {"pr_url": "fixture-pr"}, 1)[0] == "blocked"


def test_ci_must_match_verified_commit(existing, monkeypatch):
    from cpipe import pr
    (existing / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(pr, "_gh", lambda *a, **kw: SimpleNamespace(returncode=0, stdout='{"headRefOid": "wrong-head"}'))
    assert pr.ci(existing, {"pr_url": "fixture-pr"}, 1)[0] == "blocked"


def test_final_readiness_cannot_ignore_pending_ci(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    key = r.gate_key()
    r.state.update(verified_head=key["head"], verification_receipt=key, checks_receipt=key, pr_url="fixture-pr")
    monkeypatch.setattr("cpipe.round.pr.ci", lambda *a: ("blocked", "CI is pending"))
    monkeypatch.setattr("cpipe.round.pr.ready", lambda *a: pytest.fail("pending CI cannot ship"))
    assert r.final("", "")[0] == "blocked"
    assert "ci_head" not in r.state
