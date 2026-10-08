"""Immutable engineering contracts for the optional delivery core."""
from dataclasses import dataclass
from enum import Enum

SCHEMA_VERSION = 1


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    WAIVED = "waived"


@dataclass(frozen=True)
class Envelope:
    delivery_id: str
    session_id: str
    profile_id: str
    repo: str
    branch: str
    base: str
    scope: str
    required: tuple[str, ...]
    capabilities: frozenset[str]


@dataclass(frozen=True)
class Receipt:
    obligation: str
    artifact: str
    scope: str
    inputs: str
    environment: str
    command: str
    outcome: Outcome
    observed: bool
