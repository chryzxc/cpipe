"""A Hermes profile as the engine (``kind: hermes``, ``profile: coder``):
``hermes -p <profile> chat -Q -q <prompt> --format stream-json --in <worktree> --pass-session-id [--resume <id>]``.

Hermes has no structured output: the prompt asks for a fenced JSON block, parsed from the final message.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import RESULT_SCHEMA, EngineResult, child_env, fenced, outcome, schema_note


def command(cfg: dict, prompt: str, cwd: Path, session: Optional[str], profile: Optional[str] = None) -> list[str]:
    argv = [cfg.get("bin") or "hermes", "-p", profile or cfg.get("profile") or "default", "chat", "-Q", "-q", prompt,
            "--format", "stream-json", "--in", str(cwd), "--pass-session-id", "--yolo"]
    return argv + (["--resume", session] if session else [])


def run(cfg: dict, prompt: str, cwd: Path, session: Optional[str] = None, schema: dict = RESULT_SCHEMA,
        log: Callable[[str], None] = print, reasoning: Optional[str] = None, readonly: bool = False,
        profile: Optional[str] = None) -> EngineResult:
    if readonly:
        prompt += "\n\nDo not modify any file."
    proc = subprocess.Popen(command(cfg, prompt + schema_note(schema), cwd, session, profile), cwd=cwd,
                            env=child_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace")
    final, other = {}, []
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except ValueError:
            other.append(line.strip())
            continue
        if ev.get("type") == "result":
            final = ev
        elif ev.get("type") == "tool" or ev.get("tool"):
            log(f"hermes: {str(ev.get('tool') or ev.get('name') or '')[:200]}")
    proc.wait()
    t = final.get("tokens") or {}
    tokens = {"input_tokens": t.get("input", 0), "cached_input_tokens": t.get("cache_read", 0),
              "output_tokens": t.get("output", 0)}
    text = str(final.get("text") or "")
    return outcome(fenced(text), text[-1500:] or "\n".join(other[-5:]) or f"hermes exited {proc.returncode}",
                   final.get("session_id") or session, tokens)
