"""Claude Code: ``claude -p --output-format json --json-schema S``; ``--resume <session>`` continues the session.

The prompt goes in on stdin (``--disallowedTools`` takes a list and would swallow a trailing prompt argument).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import RESULT_SCHEMA, EngineResult, child_env, outcome

READONLY = ["Edit", "Write", "NotebookEdit"]


def command(cfg: dict, cwd: Path, session: Optional[str], schema: dict, readonly: bool = False,
            reasoning: Optional[str] = None) -> list[str]:
    argv = [cfg.get("bin") or "claude", "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
            "--permission-mode", "bypassPermissions", "--add-dir", str(cwd)]
    if cfg.get("model"):
        argv += ["--model", cfg["model"]]
    if reasoning or cfg.get("reasoning"):
        argv += ["--effort", reasoning or cfg["reasoning"]]
    if session:
        argv += ["--resume", session]
    if readonly:
        argv += ["--disallowedTools", *READONLY]
    return argv


def run(cfg: dict, prompt: str, cwd: Path, session: Optional[str] = None, schema: dict = RESULT_SCHEMA,
        log: Callable[[str], None] = print, reasoning: Optional[str] = None, readonly: bool = False,
        profile: Optional[str] = None) -> EngineResult:
    proc = subprocess.run(command(cfg, cwd, session, schema, readonly, reasoning), input=prompt, cwd=cwd, env=child_env(),
                          capture_output=True, text=True, errors="replace")
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return outcome(None, (proc.stdout + proc.stderr).strip()[-1500:] or f"claude exited {proc.returncode}",
                       session, {})
    usage = out.get("usage") or {}
    tokens = {"input_tokens": usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
              + usage.get("cache_creation_input_tokens", 0),
              "cached_input_tokens": usage.get("cache_read_input_tokens", 0),
              "output_tokens": usage.get("output_tokens", 0)}
    data = None if out.get("is_error") else out.get("structured_output")
    log(f"claude: {str(out.get('result') or '')[:300]}")
    return outcome(data, str(out.get("result") or proc.stderr), out.get("session_id") or session, tokens)
