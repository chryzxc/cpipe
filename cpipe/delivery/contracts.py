"""Immutable validated engineering contracts, not an execution sandbox."""
from dataclasses import dataclass, fields
from enum import Enum
import math
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
CAPABILITIES = frozenset({"local", "push", "draft_pr", "mark_ready"})


def identity(value, name):
    if type(value) is not str or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"invalid {name}")
    return value


def string_tuple(value, name):
    if type(value) is not tuple:
        raise ValueError(f"{name} must be an immutable tuple")
    for item in value:
        identity(item, name)
    if len(set(value)) != len(value):
        raise ValueError(f"duplicate {name}")


def relative_path(value):
    identity(value, "path")
    parts = value.split("/")
    if (PurePosixPath(value).is_absolute() or "\\" in value or ":" in parts[0]
            or any(p in {"", ".", ".."} for p in parts)):
        raise ValueError("path must be a confined relative POSIX path")
    return value


def confined_path(worktree, relative):
    relative_path(relative)
    root = Path(worktree).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("worktree must be a directory")
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError("path escapes worktree")
    return target


@dataclass(frozen=True)
class Versioned:
    def to_dict(self):
        result: dict[str, object] = {"schema": SCHEMA_VERSION}
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, Enum):
                value = value.value
            elif type(value) is frozenset:
                value = sorted(value)
            elif type(value) is tuple:
                value = list(value)
            result[field.name] = value
        return result

    @classmethod
    def _payload(cls, data):
        if type(data) is not dict or type(data.get("schema")) is not int or data["schema"] != SCHEMA_VERSION:
            raise ValueError("unsupported schema")
        names = {f.name for f in fields(cls)}
        if set(data) != names | {"schema"}:
            raise ValueError("invalid fields")
        return {name: data[name] for name in names}


# Why a check produced no PASS/FAIL. Hints are safe to show workers and users.
UNAVAILABLE_REASONS = {
    "timed_out": "the command did not finish within its timeout",
    "command_not_found": "the command could not be executed (not installed or not on PATH)",
    "killed": "the command was killed by a signal",
    "no_report": "no readable JUnit report was written to the {report} path",
    "no_test_cases": "the report contains no test cases (wrong file or filter?)",
    "criterion_not_found": "no reported test is named like an approved criterion; name the test exactly as the criterion",
    "criterion_ambiguous": "several reported tests match one criterion; make test names unique",
    "test_skipped": "a watched test was skipped",
    "test_crashed": "a watched test crashed (import/type/runtime error) instead of failing an assertion",
    "other_failures": "the watched tests passed but the command still failed (other tests, lint or build errors)",
    "criteria_not_in_output": "the criteria did not appear in the command output",
}


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    WAIVED = "waived"


@dataclass(frozen=True)
class Envelope(Versioned):
    delivery_id: str
    session_id: str
    profile_id: str
    repo: str
    branch: str
    base: str
    scope: str
    required: tuple[str, ...]
    capabilities: frozenset[str]

    def __post_init__(self):
        for name in ("delivery_id", "session_id", "profile_id", "repo", "branch", "base", "scope"):
            identity(getattr(self, name), name)
        string_tuple(self.required, "required")
        if type(self.capabilities) is not frozenset or not self.capabilities <= CAPABILITIES:
            raise ValueError("unsupported capabilities")

    @classmethod
    def from_dict(cls, data):
        values = cls._payload(data)
        for name in ("required", "capabilities"):
            if type(values[name]) is not list:
                raise ValueError(f"{name} must be a list")
            string_tuple(tuple(values[name]), name)
        values["required"] = tuple(values["required"])
        values["capabilities"] = frozenset(values["capabilities"])
        return cls(**values)


@dataclass(frozen=True)
class Receipt(Versioned):
    obligation: str
    artifact: str
    scope: str
    inputs: str
    environment: str
    command: str
    outcome: Outcome
    observed: bool
    reason: str = ""  # why a result is unavailable; a fixed label, never output

    def __post_init__(self):
        for name in ("obligation", "artifact", "scope", "inputs", "environment", "command"):
            identity(getattr(self, name), name)
        if type(self.outcome) is not Outcome or type(self.observed) is not bool:
            raise ValueError("invalid outcome or observed flag")
        if self.reason not in UNAVAILABLE_REASONS and self.reason != "":
            raise ValueError("invalid receipt reason")

    @classmethod
    def from_dict(cls, data):
        if type(data) is dict:
            data = {"reason": "", **data}  # receipts journaled before reasons existed
        values = cls._payload(data)
        if type(values["outcome"]) is not str:
            raise ValueError("invalid outcome")
        values["outcome"] = Outcome(values["outcome"])
        return cls(**values)


@dataclass(frozen=True)
class Budget(Versioned):
    seconds: float
    calls: int

    def __post_init__(self):
        if (type(self.seconds) not in {float, int} or not math.isfinite(self.seconds)
                or self.seconds <= 0 or type(self.calls) is not int or self.calls <= 0):
            raise ValueError("budget must be finite and positive")

    @classmethod
    def from_dict(cls, data):
        return cls(**cls._payload(data))
