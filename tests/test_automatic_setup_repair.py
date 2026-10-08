"""Automatic setup repair stays bounded and never turns an unavailable gate green."""
from cpipe import graph
from cpipe.round import Round


def runner(*, adopted=False):
    r = Round.__new__(Round)
    r.state = {"adopted": adopted}
    r.cfg = {"limits": {"max_fixes": 2}}
    return r


def test_setup_failure_returns_to_builder_then_stops_at_limit():
    r = runner()
    for stage in ("checks", "verify"):
        outcome, prompt = r.repair_unavailable(stage, ["[unavailable] missing local tool"])
        assert outcome == "fix"
        assert graph.next_node(stage, outcome) == "build"
        assert "Do not skip or weaken gates" in prompt
        assert "needs_user" in prompt
        assert "verification_receipt" not in r.state
    outcome, detail = r.repair_unavailable("qa", ["local server unavailable"])
    assert outcome == "blocked"
    assert r.state["setup_repairs"] == 2
    assert "local server unavailable" in detail


def test_read_only_adopted_work_is_not_rewritten_to_repair_setup():
    r = runner(adopted=True)
    assert r.repair_unavailable("checks", ["missing tool"])[0] == "blocked"
    assert "setup_repairs" not in r.state


def test_setup_repair_respects_existing_fix_budget():
    r = runner()
    r.state["fixes"] = 2
    assert r.repair_unavailable("checks", ["missing tool"])[0] == "blocked"
    assert "setup_repairs" not in r.state
