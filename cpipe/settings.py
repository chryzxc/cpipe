"""``$HERMES_HOME/cpipe.yaml``: which engines run deliveries, and the loop limits. Every key is optional::

    default_engine: codex
    fallback_engine: claude
    review_engine: claude
    engines:
      codex:  {bin: <path>, model: gpt-6.1-sol, reasoning: medium, concurrency: 3}
      claude: {bin: claude, model: claude-opus-5-5, concurrency: 2}
      coder:  {kind: hermes, profile: coder, concurrency: 2}      # kind: codex | claude | hermes | opencode
    limits: {checks: 3, qa: 2, review: 2, ci: 2, round_minutes: 90}
    env_pass: [GH_TOKEN]        # extra variables the round (and its engine) may see

Each engine is a runtime lane ``cp:<engine>`` running ``python -m cpipe.round``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "default_engine": "hermes",
    "fallback_engine": None,
    "review_engine": "hermes",
    "roles": {},
    "engines": {"hermes": {"bin": "hermes", "concurrency": 3}},
    "limits": {"tests": 2, "checks": 3, "qa": 2, "review": 2, "ci": 2, "round_minutes": 90},
    "env_pass": [],
    "projects_root": "~/Projects",  # where a new project's repo is started
}
# What a round needs besides the runtime's base allowlist: the Hermes home, and engine / gh auth.
ENV_PASS = ("HERMES_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
            "OPENROUTER_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK")
PR_MINUTES = 20  # push, PR and CI watch after the engine's round wall clock


def path() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser() / "cpipe.yaml"


def load(raw: Any = None) -> dict:
    if raw is None:
        p = path()
        raw = (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError("cpipe.yaml must be a mapping")
    cfg = {**DEFAULTS, **raw}
    cfg["limits"] = {**DEFAULTS["limits"], **(raw.get("limits") or {})}
    if not cfg["engines"] or not isinstance(cfg["engines"], dict):
        raise ValueError("cpipe.yaml engines must be a non-empty mapping")
    if "default_engine" not in raw and raw.get("engines") and "hermes" not in cfg["engines"]:
        cfg["default_engine"] = next(iter(cfg["engines"]))
    roles = cfg.get("roles", {})
    if not isinstance(roles, dict):
        raise ValueError("roles must be a mapping of plan/build/review/qa to configured engines")
    for role, engine in roles.items():
        if role not in ("plan", "build", "review", "qa") or not isinstance(engine, str) or engine not in cfg["engines"]:
            raise ValueError(f"roles: invalid role or engine: {role!r}: {engine!r}")
    if cfg["default_engine"] not in cfg["engines"]:
        raise ValueError(f"default_engine {cfg['default_engine']!r} is not under engines")
    return cfg


def runtime_raw(cfg: dict) -> dict:
    """The runtime config (``config.parse_config`` input): one ``cp:<engine>`` lane per engine."""
    from .runtime.scheduler import _hermes_root, _package_root
    pythonpath = os.pathsep.join((_package_root(), _hermes_root()))
    timeout = (int(cfg["limits"]["round_minutes"]) + PR_MINUTES) * 60
    lanes = {name: {"assignee": f"cp:{name}", "adapter": "round",
                    "concurrency": int((body or {}).get("concurrency", 1)), "timeout_seconds": timeout,
                    "on_success": "complete", "on_failure": "block",
                    "env": {"pass": [*ENV_PASS, *cfg.get("env_pass", [])], "set": {"PYTHONPATH": pythonpath}}}
             for name, body in cfg["engines"].items()}
    return {k: v for k, v in {"lanes": lanes, "boards": cfg.get("boards")}.items() if v}
