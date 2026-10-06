"""Engines: one coding-agent CLI each. ``run(cfg, prompt, cwd, session=None, schema=RESULT_SCHEMA, log=print)``
starts a session (or resumes ``session``) in ``cwd`` and returns an :class:`EngineResult`."""

from __future__ import annotations

import importlib
import os
import shutil
from dataclasses import dataclass, field
from typing import Optional

# Every engine turn ends with this (structured output where the CLI has it). Strict: all keys required.
RESULT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["status", "summary", "assumptions", "question"],
    "properties": {
        "status": {"type": "string", "enum": ["done", "needs_user"]},
        "summary": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "question": {"type": "string"},
    },
}


@dataclass
class EngineResult:
    status: str                   # done | needs_user | rate_limited | unavailable | failed
    summary: str = ""
    session: Optional[str] = None
    data: dict = field(default_factory=dict)   # the structured result (assumptions, question, findings…)
    tokens: dict = field(default_factory=dict)


def get(name: str):
    return importlib.import_module(f"{__name__}.{name}")


def available(name: str, cfg: dict) -> tuple[bool, str]:
    exe = cfg.get("bin") or name
    return (True, "") if shutil.which(exe) else (False, f"{name}: executable not found: {exe}")


def child_env() -> dict:
    """The engine's environment: the round's, minus what only the round needs (Hermes' python path)."""
    return {k: v for k, v in os.environ.items()
            if k not in ("PYTHONPATH", "PYTHONHOME") and not k.startswith("WR_")}
