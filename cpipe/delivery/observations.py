"""Observed check results. Only trusted adapters may construct observations."""
from dataclasses import dataclass
from .contracts import Receipt, Outcome, identity, relative_path


@dataclass(frozen=True)
class Proof:
    criterion: str
    outcome: Outcome
    kind: str
    validator: str

    def __post_init__(self):
        identity(self.criterion, "criterion")
        identity(self.validator, "validator")
        if type(self.outcome) is not Outcome or self.kind not in {"assertion", "criterion", "setup"}:
            raise ValueError("invalid behavior proof")


@dataclass(frozen=True)
class Observation:
    before: str
    after: str
    argv: tuple[str, ...]
    cwd: str
    exit_code: int | None
    output_digest: str
    evidence_ref: str
    runner_id: str
    actor_id: str
    timed_out: bool = False
    cancelled: bool = False
    proof: Proof | None = None

    def __post_init__(self):
        for name in ("before", "after", "cwd", "output_digest", "runner_id", "actor_id"):
            identity(getattr(self, name), name)
        relative_path(self.evidence_ref)
        if type(self.argv) is not tuple or not self.argv:
            raise ValueError("argv must be an immutable nonempty tuple")
        for argument in self.argv:
            identity(argument, "argument")
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ValueError("exit code must be an integer or unknown")
        if type(self.timed_out) is not bool or type(self.cancelled) is not bool:
            raise ValueError("invalid timeout/cancellation flag")
        if self.proof is not None and type(self.proof) is not Proof:
            raise ValueError("invalid proof")


def to_receipt(value, obligation, scope, inputs, environment, command):
    outcome = Outcome.UNAVAILABLE
    proof = value.proof
    if (value.before == value.after and not value.timed_out and not value.cancelled
            and proof is not None and proof.criterion == obligation):
        if value.exit_code == 0 and proof.outcome is Outcome.PASS and proof.kind == "criterion":
            outcome = Outcome.PASS
        elif (value.exit_code is not None and value.exit_code > 0
              and proof.outcome is Outcome.FAIL and proof.kind == "assertion"):
            outcome = Outcome.FAIL
    return Receipt(obligation, value.after, scope, inputs, environment, command, outcome, True)
