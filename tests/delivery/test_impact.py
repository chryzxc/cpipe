import pytest
from cpipe.delivery.impact import Obligation, affected


def graph():
    return [Obligation("focused", frozenset({"src/a.py", "tests/test_a.py"})),
            Obligation("integration", frozenset({"requirements.lock"}), ("focused",)),
            Obligation("unrelated", frozenset({"src/b.py"}))]


def test_transitive_consumers_rerun_not_unrelated_checks():
    assert affected(graph(), {"src/a.py"}) == {"focused", "integration"}
    assert affected(graph(), {"src/b.py"}) == {"unrelated"}
    assert affected(graph(), set()) == set()


def test_unknown_inputs_or_authority_rerun_conservatively():
    assert affected(graph(), {"new.py"}) == {"focused", "integration", "unrelated"}
    assert affected(graph(), set(), authority=False) == {"focused", "integration", "unrelated"}
    assert affected([Obligation("unknown", None)], set()) == {"unknown"}


def test_invalid_graph_is_rejected():
    with pytest.raises(ValueError):
        affected([Obligation("a", frozenset(), ("missing",))], set())
    with pytest.raises(ValueError):
        affected([Obligation("a", frozenset(), ("b",)),
                  Obligation("b", frozenset(), ("a",))], set())
