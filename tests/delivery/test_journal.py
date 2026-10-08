import pytest
from cpipe.delivery.journal import Journal


def test_projection_event_and_intent_survive_restart_without_duplicates(tmp_path):
    path = tmp_path / "facts.db"
    with Journal(path) as j:
        assert j.append("d1", 0, "start", {"phase": "checking"},
                        (("i1", {"action": "check", "delivery": "d1"}),)) == 1
        assert j.append("d1", 0, "start", {"phase": "checking"},
                        (("i1", {"action": "check", "delivery": "d1"}),)) == 1
        with pytest.raises(ValueError, match="idempotency"):
            j.append("d1", 0, "start", {"phase": "other"})
        with pytest.raises(RuntimeError, match="version"):
            j.append("d1", 0, "different", {"phase": "reviewing"})
    with Journal(path) as j:
        assert j.read("d1") == (1, {"phase": "checking"})
        assert j.pending() == [("i1", {"action": "check", "delivery": "d1"})]
        j.ack("i1")
        assert j.pending() == []


def test_two_connections_cannot_replace_a_newer_snapshot(tmp_path):
    with Journal(tmp_path / "facts.db") as a, Journal(tmp_path / "facts.db") as b:
        a.append("d1", 0, "first", {"phase": "checking"})
        with pytest.raises(RuntimeError, match="version"):
            b.append("d1", 0, "stale", {"phase": "ready"})
        assert b.read("d1") == (1, {"phase": "checking"})


def test_conflicting_intent_rolls_back_event_projection_and_other_intents(tmp_path):
    with Journal(tmp_path / "facts.db") as j:
        j.append("d1", 0, "first", {"phase": "checking"}, (("i1", {"action": "check"}),))
        with pytest.raises(ValueError, match="idempotency"):
            j.append("d1", 1, "second", {"phase": "ready"},
                     (("i2", {"action": "review"}), ("i1", {"action": "publish"})))
        assert j.read("d1") == (1, {"phase": "checking"})
        assert j.pending() == [("i1", {"action": "check"})]
        assert j.append("d1", 1, "second", {"phase": "reviewing"}) == 2
