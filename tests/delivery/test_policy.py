from dataclasses import replace
from cpipe.delivery.contracts import Envelope, Receipt, Outcome
from cpipe.delivery.policy import permits, ready


def envelope():
    return Envelope("d1", "s1", "p1", "repo", "branch", "base", "scope-v1",
                    ("behavior", "review"), frozenset({"local"}))


def receipt(name, **changes):
    r = Receipt(name, "tree1", "scope-v1", "inputs1", "env1", "command1",
                Outcome.PASS, True)
    return replace(r, **changes)


def test_readiness_needs_observed_current_receipts():
    e = envelope()
    rs = [receipt("behavior"), receipt("review")]
    assert ready(e, rs, "tree1")
    assert not ready(e, rs, "tree2")
    assert not ready(e, rs[:1], "tree1")
    assert not ready(e, [receipt("behavior", observed=False), rs[1]], "tree1")
    assert not ready(e, [receipt("behavior", outcome=Outcome.UNAVAILABLE), rs[1]], "tree1")
    assert not ready(e, [receipt("behavior", scope="old"), rs[1]], "tree1")


def test_latest_failure_does_not_reuse_an_earlier_pass():
    assert not ready(envelope(), [receipt("behavior"), receipt("review"),
                     receipt("behavior", outcome=Outcome.FAIL)], "tree1")


def test_publication_is_separate_and_target_bound():
    e = envelope()
    assert not permits(e, "draft_pr", "repo", "branch", "base")
    granted = replace(e, capabilities=frozenset({"local", "draft_pr"}))
    assert permits(granted, "draft_pr", "repo", "branch", "base")
    assert not permits(granted, "draft_pr", "other", "branch", "base")
    assert not permits(granted, "merge", "repo", "branch", "base")
