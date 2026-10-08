from dataclasses import replace
import json
import pytest
from cpipe.delivery.contracts import Budget, Envelope
from cpipe.delivery.schema import Actor, CheckIdentity, ExecutionPlan


def plan(**changes):
    value = ExecutionPlan(
        envelope=Envelope("d1", "s1", "default", "owner/repo", "feature", "main", "scope1",
                          ("behavior", "review"), frozenset({"local"})),
        worktree="/work/repo", common_git_dir="/work/repo/.git", base_commit="a" * 40,
        goal="Fix the approved behavior", acceptance=("Preserve adjacent behavior",),
        non_goals=("No dependencies",), write_paths=("src/a.py", "tests/test_a.py"),
        forbidden_paths=(".git", "internal"),
        actors=(Actor("builder", "hermes", "forge", "model1", "provider1"),
                Actor("reviewer", "hermes", "sentry", "model2", "provider2")),
        budget=Budget(300, 6), policy_id="policy1", check_definition="checks1",
        checks=(CheckIdentity("behavior", "inputs1", "env1", "command1"),
                CheckIdentity("review", "inputs1", "env1", "review-command")),
        interactive=True, repair_allowed=False, transient_retries=1,
        completion_target="local_only", expires_at=2000)
    return replace(value, **changes)


def test_full_plan_round_trips_and_digest_binds_every_scope_field():
    value = plan()
    assert ExecutionPlan.from_dict(json.loads(json.dumps(value.to_dict()))) == value
    assert len(value.digest()) == 64
    assert value.digest() == plan().digest()
    for changes in ({"goal": "Other goal"}, {"write_paths": ("src/b.py",)},
                    {"check_definition": "checks2"}, {"expires_at": 3000},
                    {"budget": Budget(301, 6)}, {"repair_allowed": True}):
        assert replace(value, **changes).digest() != value.digest()


@pytest.mark.parametrize("changes", [
    {"worktree": "relative"}, {"common_git_dir": "relative"},
    {"base_commit": "main"}, {"goal": ""}, {"acceptance": ()},
    {"write_paths": ("../outside",)}, {"write_paths": (".git/config",)},
    {"write_paths": ("internal/private.md",)}, {"forbidden_paths": []},
    {"actors": []}, {"actors": (Actor("builder", "hermes", "forge", "m", "p"),)},
    {"interactive": 1}, {"repair_allowed": "yes"}, {"transient_retries": True},
    {"transient_retries": 3}, {"expires_at": 0}, {"expires_at": True},
    {"completion_target": "merge"}, {"completion_target": "draft"},
    {"checks": ()}, {"budget": {"seconds": 1, "calls": 1}},
])
def test_incomplete_or_unsafe_execution_contract_is_rejected(changes):
    with pytest.raises(ValueError):
        plan(**changes)


def test_deserialization_rejects_unknown_fields_and_sparse_prototype():
    data = plan().to_dict()
    data["approval"] = True
    with pytest.raises(ValueError):
        ExecutionPlan.from_dict(data)
    with pytest.raises(ValueError):
        ExecutionPlan.from_dict(plan().envelope.to_dict())


def test_actor_and_check_fields_are_strict():
    with pytest.raises(ValueError):
        Actor("coordinator", "hermes", "forge", "m", "p")
    with pytest.raises(ValueError):
        Actor("builder", "unknown", "forge", "m", "p")
    with pytest.raises(ValueError):
        CheckIdentity("behavior", "", "environment", "command")
