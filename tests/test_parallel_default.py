from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT / "workflow" / "skills" / "my-software-delivery-orchestrator" / "SKILL.md").read_text()
PARALLEL = (ROOT / "workflow" / "skills" / "my-software-delivery-orchestrator" /
            "references" / "parallel-execution.md").read_text()
README = (ROOT / "README.md").read_text()


def test_skill_carries_parallel_by_default_rule():
    assert "Independent slices run in parallel by default" in SKILL
    assert "never wait for the operator to request parallelism" in SKILL
    assert "serial chain requires an explicitly stated dependency reason" in SKILL
    assert "more than one card in a wave" in SKILL
    assert "more than 3 cards" not in SKILL


def test_parallel_reference_carries_decomposition_contract():
    assert "## Parallel by default" in PARALLEL
    assert "never wait for the operator" in PARALLEL
    for exception in ("dependency chains", "same-module collisions", "risk-tier HIGH serialization"):
        assert exception in PARALLEL
    assert "CAPACITY_HOLD" in PARALLEL
    assert "REWORK_LOOP" in PARALLEL


def test_readme_states_default_parallelism():
    assert "parallel waves by default" in README
    assert "no prompt needed" in README
