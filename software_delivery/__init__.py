"""Hermes Software Delivery native plugin.

Registers the deterministic delivery tools (policy check, board intelligence,
mutation check), a doctor CLI command, and a passive session-metrics hook.
Policy skills, scripts, cron definitions, and config assertions live in
``workflow/`` and are deployed by ``install.sh``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

__all__ = ["register"]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "workflow" / "scripts"
_METRICS_LOG = Path.home() / ".hermes" / "logs" / "delivery-metrics.jsonl"


def _run_script(name: str, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS / name), *args],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        return json.dumps({"ok": False, "error": result.stderr.strip()[-2000:] or result.stdout.strip()[-2000:]})
    return json.dumps({"ok": True, "output": result.stdout.strip()})


def _check_policy(**kwargs) -> str:
    return _run_script("check_delivery_config.py")


def _board_intelligence(**kwargs) -> str:
    return _run_script("board_intelligence.py")


def _mutation_check(worktree: str, file_path: str, test_filter: str, **kwargs) -> str:
    wt = Path(worktree).resolve()
    git_dir = wt / ".git"
    if not git_dir.exists():
        return json.dumps({"ok": False, "error": "worktree must be a git worktree"})
    target = (wt / file_path).resolve()
    if not target.is_relative_to(wt):
        return json.dumps({"ok": False, "error": "file_path must stay inside the worktree"})
    if not target.is_file():
        return json.dumps({"ok": False, "error": f"file not found: {file_path}"})
    original = target.read_text()
    mutated = original.replace(" == ", " != ", 1)
    if mutated == original:
        mutated = original.replace(" != ", " == ", 1)
    if mutated == original:
        return json.dumps({"ok": False, "error": "no flippable equality condition found in first match"})
    try:
        target.write_text(mutated)
        test = subprocess.run(
            ["swift", "test", "--disable-automatic-resolution", "--filter", test_filter],
            cwd=wt, capture_output=True, text=True, timeout=1200,
        )
        failed = test.returncode != 0
        return json.dumps({
            "ok": True,
            "mutant_killed": failed,
            "verdict": "PASS: test fails with mutation (test bites)" if failed
                       else "FAIL: test still passes with mutation (decorative test)",
            "test_exit": test.returncode,
        })
    finally:
        target.write_text(original)


def _on_session_end(**kwargs) -> None:
    try:
        _METRICS_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": time.time(), "hook": "on_session_end"}
        for key in ("profile", "session_id", "duration", "tokens", "model"):
            if kwargs.get(key) is not None:
                record[key] = kwargs[key]
        with _METRICS_LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _noop_setup(parser) -> None:
    return None


def _doctor_command(args) -> str:
    status = [
        f"software-delivery plugin: {_REPO_ROOT}",
        f"skills bundled: {len(list((_REPO_ROOT / 'workflow' / 'skills').iterdir()))}",
        f"scripts bundled: {len(list(_SCRIPTS.glob('*.py')))}",
    ]
    policy = subprocess.run(
        [sys.executable, str(_SCRIPTS / "check_delivery_config.py")],
        capture_output=True, text=True, timeout=300,
    )
    status.append(policy.stdout.strip())
    return "\n".join(status)


_POLICY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delivery_check_policy",
        "description": "Validate delivery workflow policy: engine caps vs team-config, roster integrity, and open-card requirements. Returns findings JSON.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_INTEL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delivery_board_intelligence",
        "description": "Weekly board intelligence: per-stage wall-clock, queue waits, gate rejection rates, rework loops, oldest cards. Returns markdown digest.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_MUTATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delivery_mutation_check",
        "description": "Flip one equality condition in a file inside a disposable git worktree, run the focused swift test, and report whether the test fails. Verifies the test bites. File is restored after.",
        "parameters": {
            "type": "object",
            "properties": {
                "worktree": {"type": "string", "description": "Absolute path to the disposable git worktree"},
                "file_path": {"type": "string", "description": "Repository-relative file to mutate"},
                "test_filter": {"type": "string", "description": "swift test --filter expression"},
            },
            "required": ["worktree", "file_path", "test_filter"],
        },
    },
}


def register(ctx):
    """Register deterministic delivery tools, doctor CLI, and metrics hook."""
    ctx.register_tool(
        name="delivery_check_policy", toolset="software_delivery",
        schema=_POLICY_SCHEMA, handler=lambda args, **kw: _check_policy(),
    )
    ctx.register_tool(
        name="delivery_board_intelligence", toolset="software_delivery",
        schema=_INTEL_SCHEMA, handler=lambda args, **kw: _board_intelligence(),
    )
    ctx.register_tool(
        name="delivery_mutation_check", toolset="software_delivery",
        schema=_MUTATION_SCHEMA,
        handler=lambda args, **kw: _mutation_check(
            worktree=args["worktree"], file_path=args["file_path"], test_filter=args["test_filter"]),
    )
    ctx.register_cli_command(
        name="software-delivery", help="Software delivery plugin doctor",
        setup_fn=_noop_setup, handler_fn=lambda args: _doctor_command(args),
        description="Check software-delivery plugin status and run the policy validator.",
    )
    ctx.register_hook("on_session_end", _on_session_end)
