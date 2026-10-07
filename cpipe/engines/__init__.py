"""Engines: one coding-agent CLI each. ``run(cfg, prompt, cwd, session=None, schema=RESULT_SCHEMA, log=print,
readonly=False, reasoning=None, profile=None)`` starts a session (or resumes ``session``) in ``cwd`` and returns
an :class:`EngineResult` whose ``status`` is the schema's ``status`` (or an outage: rate_limited, unavailable,
failed).

An engine's kind is its config's ``kind`` (default: its name), so one CLI can back several engines::

    engines:
      coder: {kind: hermes, profile: coder}
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Optional

from ..runtime.result import looks_auth_failure, looks_rate_limited

KINDS = ("codex", "claude", "hermes", "opencode")
BINS = {"codex": "codex", "claude": "claude", "hermes": "hermes", "opencode": "opencode"}


def _schema(status: list[str], **props) -> dict:
    """A strict schema (codex requires every key and no extras): ``status`` + ``summary`` + ``props``."""
    props = {"status": {"type": "string", "enum": status}, "summary": {"type": "string"}, **props}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


_STRINGS = {"type": "array", "items": {"type": "string"}}
# Every build turn ends with this.
RESULT_SCHEMA = _schema(["done", "needs_user"], assumptions=_STRINGS, question={"type": "string"},
                        how_to_test={"type": "string"})
PLAN_SCHEMA = _schema(["done", "needs_user"], question={"type": "string"}, plan={"type": "string"},
                      questions=_STRINGS, parts={"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["title", "request", "after"],
    "properties": {"title": {"type": "string"}, "request": {"type": "string"},
                   "after": {"type": "array", "items": {"type": "integer"}}}}})
TESTS_SCHEMA = _schema(["done", "needs_user"], question={"type": "string"},
                       kind={"type": "string", "enum": ["feature", "fix", "refactor", "none"]},
                       guard_command={"type": "string"}, red_command={"type": "string"}, tests=_STRINGS)
QA_SCHEMA = _schema(["pass", "fail"], problems=_STRINGS, screenshots=_STRINGS)
REVIEW_SCHEMA = _schema(["approve", "changes"], findings=_STRINGS)


@dataclass
class EngineResult:
    status: str                   # the schema's status, or rate_limited | unavailable | failed
    summary: str = ""
    session: Optional[str] = None
    data: dict = field(default_factory=dict)   # the structured result
    tokens: Optional[dict] = field(default_factory=dict)


def kind(name: str, cfg: dict) -> str:
    return cfg.get("kind") or name


def get(name: str, cfg: Optional[dict] = None):
    k = kind(name, cfg or {})
    if k not in KINDS:
        raise ValueError(f"unknown engine kind {k!r} (one of {', '.join(KINDS)})")
    return importlib.import_module(f"{__name__}.{k}")


def available(name: str, cfg: dict) -> tuple[bool, str]:
    k = kind(name, cfg)
    if k not in KINDS:
        return (False, f"{name}: unknown engine kind {k!r}")
    exe = cfg.get("bin") or BINS[k]
    return (True, "") if shutil.which(exe) else (False, f"{name}: executable not found: {exe}")


def child_env() -> dict:
    """The engine's environment: the round's, minus what only the round needs (Hermes' python path)."""
    return {k: v for k, v in os.environ.items()
            if k not in ("PYTHONPATH", "PYTHONHOME") and not k.startswith(("WR_", "HERMES_KANBAN"))}


def schema_note(schema: dict) -> str:
    """For CLIs without structured output: how to end the final message (an example, not the schema itself,
    which models tend to echo back)."""
    props = schema["properties"]
    example = {k: (" | ".join(v["enum"]) if "enum" in v else [] if v["type"] == "array" else "...")
               for k, v in props.items()}
    return ("\n\nEnd your final message with exactly one fenced ```json block holding your result as one object "
            f"with these keys (status is one of the listed values):\n```json\n{json.dumps(example)}\n```")


_FENCED = re.compile(r"```(?:json)?\s*\n(\{.*?\})\s*\n```", re.S)


def fenced(text: str) -> Optional[dict]:
    """The last fenced JSON object in ``text`` that has a ``status``."""
    for raw in reversed(_FENCED.findall(text or "")):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("status"):
            return data
    return None


def outcome(data: Optional[dict], why: str, session: Optional[str], tokens: dict) -> EngineResult:
    """The structured result, else an outage classified from the CLI's last error lines."""
    if isinstance(data, dict) and data.get("status"):
        return EngineResult(data["status"], data.get("summary", ""), session, data, tokens)
    status = ("rate_limited" if looks_rate_limited(why) else "unavailable" if looks_auth_failure(why)
              else "failed")
    return EngineResult(status, why[-1500:], session, {}, tokens)
