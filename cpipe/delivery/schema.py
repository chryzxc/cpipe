"""Full frozen execution contract; prototype envelopes alone cannot authorize a worker."""
from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import re
from .contracts import Budget, Envelope, Versioned, identity, relative_path, string_tuple
from .journal import encoded


@dataclass(frozen=True)
class Actor(Versioned):
    role: str
    engine: str
    profile: str
    model: str
    provider: str

    def __post_init__(self):
        if self.role not in {"builder", "reviewer"} or self.engine not in {"hermes", "cli"}:
            raise ValueError("unsupported actor role or engine")
        for name in ("profile", "model", "provider"):
            identity(getattr(self, name), name)

    @classmethod
    def from_dict(cls, data):
        return cls(**cls._payload(data))


@dataclass(frozen=True)
class CheckIdentity(Versioned):
    obligation: str
    inputs: str
    environment: str
    command: str

    def __post_init__(self):
        for name in ("obligation", "inputs", "environment", "command"):
            identity(getattr(self, name), name)

    @classmethod
    def from_dict(cls, data):
        return cls(**cls._payload(data))


@dataclass(frozen=True)
class ExecutionPlan(Versioned):
    envelope: Envelope
    worktree: str
    common_git_dir: str
    base_commit: str
    goal: str
    acceptance: tuple[str, ...]
    non_goals: tuple[str, ...]
    write_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    actors: tuple[Actor, ...]
    budget: Budget
    policy_id: str
    check_definition: str
    checks: tuple[CheckIdentity, ...]
    interactive: bool
    repair_allowed: bool
    transient_retries: int
    completion_target: str
    expires_at: int

    def __post_init__(self):
        if type(self.envelope) is not Envelope or type(self.budget) is not Budget:
            raise ValueError("execution requires validated envelope and budget")
        for name in ("worktree", "common_git_dir"):
            value = identity(getattr(self, name), name)
            if not PurePosixPath(value).is_absolute() or ".." in value.split("/"):
                raise ValueError("execution directories must be absolute")
        if type(self.base_commit) is not str or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.base_commit):
            raise ValueError("base must be an exact commit")
        for name in ("goal", "policy_id", "check_definition"):
            identity(getattr(self, name), name)
        for name in ("acceptance", "non_goals", "write_paths", "forbidden_paths"):
            string_tuple(getattr(self, name), name)
        if not self.acceptance or not self.write_paths or not self.forbidden_paths:
            raise ValueError("acceptance and scope paths are required")
        for path in self.write_paths + self.forbidden_paths:
            relative_path(path)
        # A forbidden directory cannot be made writable by a nested allowance,
        # nor can a broad allowance encompass a forbidden child.
        for path in self.write_paths:
            for denied in self.forbidden_paths + (".git",):
                if (path == denied or path.startswith(denied + "/")
                        or denied.startswith(path + "/")):
                    raise ValueError("write allowance overlaps forbidden path")
        if (type(self.actors) is not tuple or len(self.actors) != 2
                or any(type(a) is not Actor for a in self.actors)
                or {a.role for a in self.actors} != {"builder", "reviewer"}):
            raise ValueError("builder and independent reviewer bindings are required")
        if (type(self.checks) is not tuple or any(type(c) is not CheckIdentity for c in self.checks)
                or len({c.obligation for c in self.checks}) != len(self.checks)
                or {c.obligation for c in self.checks} != set(self.envelope.required)
                or not self.checks):
            raise ValueError("required checks need exact identity definitions")
        if type(self.interactive) is not bool or type(self.repair_allowed) is not bool:
            raise ValueError("invalid execution preferences")
        if type(self.transient_retries) is not int or not 0 <= self.transient_retries <= 2:
            raise ValueError("invalid retry authority")
        if type(self.expires_at) is not int or self.expires_at <= 0:
            raise ValueError("invalid authority expiry")
        if self.completion_target not in {"local_only", "draft"}:
            raise ValueError("unsupported completion target")
        if self.completion_target == "draft" and not {"push", "draft_pr"} <= self.envelope.capabilities:
            raise ValueError("draft target requires explicit push and draft authority")

    def to_dict(self):
        data = super().to_dict()
        data.update(envelope=self.envelope.to_dict(), budget=self.budget.to_dict(),
                    actors=[a.to_dict() for a in self.actors],
                    checks=[c.to_dict() for c in self.checks])
        return data

    @classmethod
    def from_dict(cls, data):
        values = cls._payload(data)
        values["envelope"] = Envelope.from_dict(values["envelope"])
        values["budget"] = Budget.from_dict(values["budget"])
        for name in ("acceptance", "non_goals", "write_paths", "forbidden_paths", "actors", "checks"):
            if type(values[name]) is not list:
                raise ValueError(f"{name} must be a list")
            values[name] = tuple(values[name])
        values["actors"] = tuple(Actor.from_dict(a) for a in values["actors"])
        values["checks"] = tuple(CheckIdentity.from_dict(c) for c in values["checks"])
        return cls(**values)

    def digest(self):
        return hashlib.sha256(encoded(self.to_dict()).encode()).hexdigest()
