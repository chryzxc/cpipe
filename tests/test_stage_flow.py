"""Exercise late-entry review → fix → verify → draft using real Git and scripted role turns.

No live bot, board, provider, GitHub request or external coding CLI is used.
"""
from types import SimpleNamespace

import pytest

from cpipe import engines
from cpipe.engines import hermes
from cpipe.round import head
from test_stage_commands import existing, git
from test_stage_gates import frozen_runner


def test_existing_change_enters_review_reworks_and_ships_same_verified_revision(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    source = head(existing)
    r.state.update(node="setup", entry="review", adopted=True,
                   adoption={"head": source, "base_head": git(existing, "rev-parse", "main")})
    monkeypatch.setattr("cpipe.round.hermes_api.comments_after", lambda *a: [])
    monkeypatch.setattr("cpipe.round.workspace_prep.project_profile", lambda *a: {"base_branch": "main", "qa": "off"})
    monkeypatch.setattr("cpipe.round.quality.load", lambda *a: {"checks": [{"name": "value", "run": "python -B -c 'import code; assert code.value == 3'"}]})
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    calls, published = [], []

    def run(ec, prompt, cwd, **kw):
        calls.append((ec["profile"], kw.get("session"), kw.get("readonly"), prompt))
        if ec["profile"] == "sentry":
            if (existing / "code.py").read_text() == "value = 2\n":
                return engines.EngineResult("changes", data={"findings": ["REV-001: value must be 3"]}, session="review-session")
            return engines.EngineResult("approve", session="review-session")
        assert ec["profile"] == "forge", "late entry must not restart planning"
        (existing / "code.py").write_text("value = 3\n")
        git(existing, "commit", "-qam", "fix: correct reviewed value")
        return engines.EngineResult("done", data={"summary": "Correct the reviewed value"}, session="builder-session")

    monkeypatch.setattr(hermes, "run", run)
    monkeypatch.setattr("cpipe.round.pr.publish", lambda ws, state, branch: published.append(head(ws)) or ("ok", "local branch"))
    reviewed = r.run()
    assert reviewed["status"] == "needs_review" and "REV-001" in reviewed["summary"]
    assert r.state["node"] == "reviewed" and head(existing) == source
    assert len(calls) == 1 and calls[0][0] == "sentry" and calls[0][2] is True
    assert not published

    r.state.update(entry="fix", stage_requested=True, publish=False)
    verified = r.run()
    final = head(existing)
    assert r.state["node"] == "verified" and "no changes published" in verified["summary"]
    assert final != source and r.state["verified_head"] == final
    assert [(p, s) for p, s, _, _ in calls] == [("sentry", None), ("forge", None), ("sentry", "review-session")]
    assert f"{source}..{final}" in calls[-1][3] and "REV-001" in calls[-1][3]
    assert not r.state.get("tdd") and "not asserted" in verified["summary"]
    assert not published

    r.state.update(entry="ship", stage_requested=True, publish=True, ready=False)
    result = r.run()
    assert result["status"] == "needs_review" and r.state["node"] == "handoff"
    assert published == [final] and len(calls) == 3  # no duplicate model calls on unchanged revision


@pytest.mark.parametrize("result", [None, 127])
def test_unavailable_red_command_cannot_earn_tests_first_receipt(existing, tmp_path, monkeypatch, result):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    monkeypatch.setattr(r, "turn", lambda *a, **kw: engines.EngineResult("done", data={"kind": "fix", "red_command": "missing-tool"}))
    monkeypatch.setattr("cpipe.round.quality._run", lambda *a: None if result is None else SimpleNamespace(returncode=result))
    assert r.tests("", "")[0] == "blocked"
