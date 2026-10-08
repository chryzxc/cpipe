import pytest
from cpipe.delivery.recovery import decide


@pytest.mark.parametrize("category,attempt,repeats,repair,expected", [
    ("transient", 0, 0, False, "retry"),
    ("transient", 2, 0, False, "diagnose"),
    ("transient", 0, 2, False, "diagnose"),
    ("persistent", 0, 0, True, "repair"),
    ("persistent", 1, 0, True, "diagnose"),
    ("persistent", 0, 0, False, "wait"),
    ("permanent", 0, 0, True, "stop"),
    ("external_wait", 0, 0, True, "wait"),
    ("behavior", 0, 0, False, "correct"),
    ("unknown", 0, 0, True, "diagnose"),
])
def test_recovery_is_bounded(category, attempt, repeats, repair, expected):
    assert decide(category, attempt, repeats, repair) == expected


def test_exhausted_budget_never_retries():
    assert decide("transient", 0, 0, budget_remaining=False) == "stop"
