from dataclasses import replace
import math
import pytest
from cpipe.delivery.contracts import Envelope, Receipt, Outcome


def envelope():
    return Envelope("d1", "s1", "p1", "repo", "branch", "base", "scope1",
                    ("behavior", "review"), frozenset({"local"}))


def receipt():
    return Receipt("behavior", "tree1", "scope1", "inputs1", "env1", "cmd1",
                   Outcome.PASS, True)


@pytest.mark.parametrize("field,value", [
    ("delivery_id", ""), ("session_id", " "), ("profile_id", None),
    ("required", ["behavior"]), ("required", ("behavior", "behavior")),
    ("capabilities", frozenset({"merge"})), ("capabilities", {"local"}),
])
def test_invalid_envelope_is_rejected(field, value):
    with pytest.raises(ValueError):
        replace(envelope(), **{field: value})


@pytest.mark.parametrize("field,value", [
    ("observed", "true"), ("observed", 1), ("outcome", "pass"),
    ("artifact", ""), ("environment", ""), ("inputs", ""), ("command", ""),
])
def test_invalid_receipt_is_rejected(field, value):
    with pytest.raises(ValueError):
        replace(receipt(), **{field: value})


def test_versioned_serialization_roundtrips_without_mutable_aliases():
    for obj in (envelope(), receipt()):
        data = obj.to_dict()
        assert data["schema"] == 1
        assert type(obj).from_dict(data) == obj
        bad = {**data, "schema": 2}
        with pytest.raises(ValueError, match="schema"):
            type(obj).from_dict(bad)
        with pytest.raises(ValueError, match="fields"):
            type(obj).from_dict({**data, "unexpected_authority": True})
    data = envelope().to_dict()
    data["required"].append("another")
    assert envelope().required == ("behavior", "review")


@pytest.mark.parametrize("seconds,calls", [(math.nan, 1), (math.inf, 1), (-1, 1),
                                           (True, 1), (1, True), (1, 0)])
def test_budget_is_finite_positive_and_not_boolean(seconds, calls):
    from cpipe.delivery.contracts import Budget
    with pytest.raises(ValueError):
        Budget(seconds, calls)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "src/../escape", "a//b",
                                  "a\\b", "", ".", "C:/escape", "a\x00b"])
def test_invalid_scope_path_is_rejected(path):
    from cpipe.delivery.contracts import relative_path
    with pytest.raises(ValueError):
        relative_path(path)


def test_scope_resolution_rejects_symlink_escape(tmp_path):
    from cpipe.delivery.contracts import confined_path
    worktree = tmp_path / "repo"
    worktree.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (worktree / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escape"):
        confined_path(worktree, "link/change.py")
    assert confined_path(worktree, "src/new.py") == worktree / "src/new.py"
