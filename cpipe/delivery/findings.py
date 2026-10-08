"""Stable finding identities and evidence-bound, append-only dispositions.

Authority/provenance arguments are trusted controller inputs, never worker prose.
"""
from dataclasses import dataclass, replace
import re
from .contracts import Versioned, Receipt, Outcome, identity, relative_path

DISPOSITIONS = frozenset({"open", "verified", "dismissed"})


@dataclass(frozen=True)
class Finding(Versioned):
    id: str
    criterion: str
    root_cause: str
    severity: str
    in_scope: bool
    disposition: str

    def __post_init__(self):
        if type(self.id) is not str or (self.id and not re.fullmatch(r"REV-\d{3,}", self.id)):
            raise ValueError("invalid finding ID")
        identity(self.criterion, "criterion")
        identity(self.root_cause, "root cause")
        if self.severity not in {"critical", "high", "medium", "low", "info"}:
            raise ValueError("invalid severity")
        if type(self.in_scope) is not bool or self.disposition not in DISPOSITIONS:
            raise ValueError("invalid finding scope or disposition")

    @classmethod
    def from_dict(cls, data):
        return cls(**cls._payload(data))


@dataclass(frozen=True)
class FindingDecision(Versioned):
    previous: str
    disposition: str
    verdict: str
    reviewed_range: str
    criterion: str
    artifact: str
    scope: str
    evidence_ref: str
    receipt: Receipt
    policy_authorized: bool
    scope_authorized: bool

    def __post_init__(self):
        if (self.previous not in DISPOSITIONS or self.disposition not in DISPOSITIONS
                or self.previous == self.disposition):
            raise ValueError("invalid disposition transition")
        for name in ("verdict", "reviewed_range", "criterion", "artifact", "scope"):
            identity(getattr(self, name), name)
        relative_path(self.evidence_ref)
        if self.policy_authorized is not True or type(self.scope_authorized) is not bool:
            raise ValueError("disposition requires policy authority")
        if (type(self.receipt) is not Receipt or not self.receipt.observed
                or (self.receipt.artifact, self.receipt.scope, self.receipt.obligation)
                != (self.artifact, self.scope, self.criterion)):
            raise ValueError("disposition requires current criterion evidence")
        expected = Outcome.FAIL if self.disposition == "open" else Outcome.PASS
        if self.receipt.outcome is not expected:
            raise ValueError("disposition evidence does not prove the requested state")

    def to_dict(self):
        data = super().to_dict()
        data["receipt"] = self.receipt.to_dict()
        return data

    @classmethod
    def from_dict(cls, data):
        values = cls._payload(data)
        values["receipt"] = Receipt.from_dict(values["receipt"])
        return cls(**values)


@dataclass(frozen=True)
class FindingRecord(Versioned):
    finding: Finding
    history: tuple[FindingDecision, ...] = ()

    def __post_init__(self):
        if type(self.finding) is not Finding or not self.finding.id:
            raise ValueError("persistent finding requires stable ID")
        if type(self.history) is not tuple:
            raise ValueError("history must be immutable")
        for i, decision in enumerate(self.history):
            if type(decision) is not FindingDecision or decision.criterion != self.finding.criterion:
                raise ValueError("invalid finding history")
            if i and self.history[i - 1].disposition != decision.previous:
                raise ValueError("disconnected finding history")
            if (decision.disposition == "dismissed" and self.finding.in_scope
                    and self.finding.severity in {"critical", "high", "medium"}
                    and not decision.scope_authorized):
                raise ValueError("serious in-scope dismissal requires explicit scope authority")
        if self.history and self.history[-1].disposition != self.finding.disposition:
            raise ValueError("finding projection does not match history")

    def to_dict(self):
        return {"schema": 1, "finding": self.finding.to_dict(),
                "history": [d.to_dict() for d in self.history]}

    @classmethod
    def from_dict(cls, data):
        values = cls._payload(data)
        if type(values["history"]) is not list:
            raise ValueError("history must be a list")
        return cls(Finding.from_dict(values["finding"]),
                   tuple(FindingDecision.from_dict(d) for d in values["history"]))


def disposition(value, state, *, artifact, scope, verdict, reviewed_range, evidence_ref,
                receipt, policy_authorized=False, scope_authorized=False):
    if type(value) is not FindingRecord:
        raise ValueError("invalid finding record")
    decision = FindingDecision(value.finding.disposition, state, verdict, reviewed_range,
                               value.finding.criterion, artifact, scope, evidence_ref, receipt,
                               policy_authorized, scope_authorized)
    return FindingRecord(replace(value.finding, disposition=state), value.history + (decision,))


def merge_findings(prior, incoming):
    result = list(prior)
    known = {(f.criterion, f.root_cause): f for f in result}
    ids = {f.id for f in result}
    if len(ids) != len(result) or len(known) != len(result) or "" in ids:
        raise ValueError("duplicate or missing persistent finding identity")
    next_number = max((int(f.id[4:]) for f in result), default=0) + 1
    for finding in incoming:
        signature = (finding.criterion, finding.root_cause)
        if signature in known:
            continue
        finding = replace(finding, id=f"REV-{next_number:03d}")
        next_number += 1
        result.append(finding)
        known[signature] = finding
    return result


def blocking(findings):
    return any(f.in_scope and f.disposition not in {"verified", "dismissed"}
               for f in findings)


def needs_diagnosis(cycles, before, after):
    if type(cycles) is not int or cycles < 0:
        raise ValueError("invalid review cycle count")
    return cycles >= 3 or (cycles >= 2 and bool(after) and set(after) == set(before))
