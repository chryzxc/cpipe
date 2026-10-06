"""opencode: ``opencode run --format json -m <provider/model> --dir <worktree> [--session <id>] <prompt>``.

No structured output: the prompt asks for a fenced JSON block, parsed from the session's text.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import RESULT_SCHEMA, EngineResult, child_env, fenced, outcome, schema_note


def command(cfg: dict, prompt: str, cwd: Path, session: Optional[str]) -> list[str]:
    argv = [cfg.get("bin") or "opencode", "run", "--format", "json", "--auto", "--dir", str(cwd)]
    if cfg.get("model"):
        argv += ["-m", cfg["model"]]
    if session:
        argv += ["--session", session]
    return argv + [prompt]


def run(cfg: dict, prompt: str, cwd: Path, session: Optional[str] = None, schema: dict = RESULT_SCHEMA,
        log: Callable[[str], None] = print, reasoning: Optional[str] = None, readonly: bool = False,
        profile: Optional[str] = None) -> EngineResult:
    if readonly:
        prompt += "\n\nDo not modify any file."
    proc = subprocess.Popen(command(cfg, prompt + schema_note(schema), cwd, session), cwd=cwd, env=child_env(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace")
    texts, errors, tokens = [], [], {}
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except ValueError:
            errors.append(line.strip())
            continue
        session = ev.get("sessionID") or session
        part = ev.get("part") or {}
        if ev.get("type") == "text":
            texts.append(part.get("text", ""))
            log(f"opencode: {part.get('text', '')[:300]}")
        elif ev.get("type") == "step_finish":
            t = part.get("tokens") or {}
            for k, v in (("input_tokens", t.get("input", 0)), ("output_tokens", t.get("output", 0)),
                         ("cached_input_tokens", (t.get("cache") or {}).get("read", 0))):
                tokens[k] = tokens.get(k, 0) + (v or 0)
        elif ev.get("type") == "error":
            errors.append(json.dumps(ev.get("error") or ev)[:500])
    proc.wait()
    text = "\n".join(texts)
    return outcome(fenced(text), "\n".join(errors[-5:]) or text[-1500:] or f"opencode exited {proc.returncode}",
                   session, tokens)
