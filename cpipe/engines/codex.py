"""Codex: ``codex exec --json --output-schema S -o F``; ``codex exec resume <thread>`` continues the session.

The sandbox is workspace-write with git writable: workspace-write makes the git dir read-only, and a worktree
commits into both its own git dir (``.git/worktrees/<id>``: index, HEAD) and the shared one (objects, refs). stdin is closed (exec otherwise waits for more input).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import RESULT_SCHEMA, EngineResult, child_env, outcome


def git_dirs(cwd: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir",
                          "--absolute-git-dir"], capture_output=True, text=True)
    return list(dict.fromkeys(out.stdout.split()))


def command(cfg: dict, prompt: str, cwd: Path, session: Optional[str], schema_file: Path,
            out_file: Path, reasoning: Optional[str] = None, readonly: bool = False,
            profile: Optional[str] = None) -> list[str]:
    argv = [cfg.get("bin") or "codex", "exec", *(["resume", session] if session else []),
            "--json", "--skip-git-repo-check", "--output-schema", str(schema_file), "-o", str(out_file)]
    if readonly:
        argv += ["-c", 'sandbox_mode="read-only"']
    else:
        argv += ["-c", 'sandbox_mode="workspace-write"',
                 "-c", f"sandbox_workspace_write.writable_roots={json.dumps(git_dirs(cwd))}",
                 "-c", f"sandbox_workspace_write.network_access={str(cfg.get('network', True)).lower()}"]
    if profile:
        argv += ["-c", f'profile="{profile}"']
    if cfg.get("model"):
        argv += ["-m", cfg["model"]]
    if reasoning or cfg.get("reasoning"):
        argv += ["-c", f'model_reasoning_effort="{reasoning or cfg["reasoning"]}"']
    if not session:
        argv += ["-C", str(cwd)]
    return argv + [prompt]


def run(cfg: dict, prompt: str, cwd: Path, session: Optional[str] = None, schema: dict = RESULT_SCHEMA,
        log: Callable[[str], None] = print, reasoning: Optional[str] = None, readonly: bool = False,
        profile: Optional[str] = None) -> EngineResult:
    work = cwd / ".cpipe"
    work.mkdir(exist_ok=True)
    tag = "review" if readonly else "build"  # a review runs beside QA's turn: separate files
    schema_file, out_file = work / f"schema-{tag}.json", work / f"last-message-{tag}.json"
    schema_file.write_text(json.dumps(schema))
    out_file.unlink(missing_ok=True)
    proc = subprocess.Popen(command(cfg, prompt, cwd, session, schema_file, out_file, reasoning, readonly, profile),
                            cwd=cwd, env=child_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace")
    thread, tokens, errors = session, {}, []
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except ValueError:
            errors.append(line.strip())
            continue
        kind, item = ev.get("type"), ev.get("item") or {}
        if kind == "thread.started":
            thread = ev.get("thread_id") or thread
        elif kind == "turn.completed":
            for k, v in (ev.get("usage") or {}).items():
                tokens[k] = tokens.get(k, 0) + v
        elif kind in ("error", "turn.failed"):
            errors.append(ev.get("message") or (ev.get("error") or {}).get("message") or line.strip())
        elif kind == "item.completed" and item.get("type") == "command_execution":
            log(f"codex $ {item.get('command')} -> {item.get('exit_code')}")
        elif kind == "item.completed" and item.get("type") == "agent_message":
            log(f"codex: {item.get('text', '')[:300]}")
    proc.wait()
    try:
        data = json.loads(out_file.read_text())
    except (OSError, ValueError):
        data = None
    return outcome(data, "\n".join(errors[-5:]) or f"codex exited {proc.returncode} without a result", thread, tokens)
