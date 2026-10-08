from dataclasses import replace
import pytest
from cpipe.delivery.contracts import Outcome
from cpipe.delivery.observations import Observation, Proof, to_receipt


def observation():
    return Observation("tree1", "tree1", ("python", "-m", "pytest"), "repo",
                       0, "digest1", "checks/result.txt", "runner1", "actor1",
                       proof=Proof("behavior", Outcome.PASS, "criterion", "validator1"))


def receipt(value):
    return to_receipt(value, "behavior", "scope1", "inputs1", "env1", "cmd1")


def test_current_observed_command_with_proof_produces_pass():
    r = receipt(observation())
    assert r.outcome is Outcome.PASS and r.observed
    assert r.artifact == "tree1"


@pytest.mark.parametrize("change", [
    {"proof": None}, {"after": "tree2"}, {"timed_out": True}, {"cancelled": True},
    {"exit_code": None}, {"exit_code": -9},
    {"proof": Proof("different_criterion", Outcome.PASS, "criterion", "validator1")},
    {"proof": Proof("behavior", Outcome.FAIL, "assertion", "validator1")},
])
def test_missing_stale_or_inconsistent_proof_is_unavailable(change):
    assert receipt(replace(observation(), **change)).outcome is Outcome.UNAVAILABLE


def test_setup_error_is_not_behavior_red():
    setup = replace(observation(), exit_code=1,
                    proof=Proof("behavior", Outcome.FAIL, "setup", "validator1"))
    assert receipt(setup).outcome is Outcome.UNAVAILABLE


def test_verified_specific_assertion_failure_is_behavior_red():
    failure = replace(observation(), exit_code=1,
                      proof=Proof("behavior", Outcome.FAIL, "assertion", "validator1"))
    assert receipt(failure).outcome is Outcome.FAIL


@pytest.mark.parametrize("field,value", [("timed_out", "false"), ("cancelled", 0),
                                        ("exit_code", True), ("argv", ["python"]),
                                        ("runner_id", ""), ("actor_id", "")])
def test_malformed_observation_is_rejected(field, value):
    with pytest.raises(ValueError):
        replace(observation(), **{field: value})
