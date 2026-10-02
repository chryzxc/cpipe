"""cpipe native plugin.

Registers the deterministic delivery tools (policy check, board intelligence,
mutation check, submit, status/watch), a doctor/status/log CLI command, a passive
session-metrics hook, the dispatch-liveness hooks (engine tick telemetry + stall
notices, see ``liveness.py``), and the chat-side monitor hooks (``status.py``).
Policy skills, scripts, cron definitions, and config assertions live in
``workflow/`` and are deployed by ``install.sh``.
"""

from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import block_reasons, card_gate, exit_handoff, headless_clarify, liveness, review_gate, status, submit, workspace_prep

__all__ = ["register"]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "workflow" / "scripts"
_METRICS_LOG = Path.home() / ".hermes" / "logs" / "delivery-metrics.jsonl"

_SUPPORTED_STACKS = "swift, python/pytest, node/npm, make"


def _buildcmds():
    spec = importlib.util.spec_from_file_location("buildcmds", _SCRIPTS / "buildcmds.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _mutation_check(worktree: str, file_path: str, test_filter: str, test_cmd: str | None = None, **kwargs) -> str:
    wt = Path(worktree).resolve()
    git_dir = wt / ".git"
    if not git_dir.exists():
        return json.dumps({"ok": False, "error": "worktree must be a git worktree"})
    target = (wt / file_path).resolve()
    if not target.is_relative_to(wt):
        return json.dumps({"ok": False, "error": "file_path must stay inside the worktree"})
    if not target.is_file():
        return json.dumps({"ok": False, "error": f"file not found: {file_path}"})
    recipe = _buildcmds().detect_build(wt)
    if test_cmd:
        test_argv = shlex.split(test_cmd)
        stack = "custom"
    elif recipe and recipe["test"]:
        test_argv = list(recipe["test"])
        if recipe["filter_flag"]:
            test_argv += [recipe["filter_flag"], test_filter]
        stack = recipe["stack"]
    else:
        return json.dumps({
            "ok": False,
            "error": f"no supported build recipe found (supported: {_SUPPORTED_STACKS}) — pass test_cmd to override",
        })
    original = target.read_text()
    mutated = original.replace(" == ", " != ", 1)
    if mutated == original:
        mutated = original.replace(" != ", " == ", 1)
    if mutated == original:
        return json.dumps({"ok": False, "error": "no flippable equality condition found in first match"})
    try:
        target.write_text(mutated)
        test = subprocess.run(
            test_argv,
            cwd=wt, capture_output=True, text=True, timeout=1200,
        )
        failed = test.returncode != 0
        return json.dumps({
            "ok": True,
            "stack": stack,
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
        status._journal("session.end", None, **{k: v for k, v in record.items() if k not in ("ts", "hook")})
    except Exception:
        pass


def _cli_setup(parser) -> None:
    parser.add_argument("action", nargs="?", choices=["doctor", "queue", "status", "log"], default="doctor",
                        help="doctor (default): plugin status; queue: stuck cards as JSON (Herm Mission Control); "
                             "status: every open card's monitor verdict; log: the delivery journal")
    parser.add_argument("--card", help="status/log: one card or chain root")
    parser.add_argument("--since", help="log: only entries newer than <n>m|h|d, e.g. 2h")
    parser.add_argument("--kind", help="log: only kinds with this prefix, e.g. monitor. or waste.")
    parser.add_argument("--json", action="store_true", help="status/log: raw JSON")


def _queue_json() -> str:
    """Board triage's grouped stuck-card queue, read-only."""
    import sqlite3
    spec = importlib.util.spec_from_file_location("board_triage", _SCRIPTS / "board_triage.py")
    triage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(triage)
    conn = sqlite3.connect(f"file:{triage.DB}?mode=ro", uri=True)
    try:
        return json.dumps(triage.queue(conn))
    finally:
        conn.close()


def _status_text(args) -> str:
    result = status.status(getattr(args, "card", None))
    if getattr(args, "json", False) or not result.get("ok"):
        return json.dumps(result, indent=1, default=str)
    lines = [" · ".join(f"{k} {v}" for k, v in result["counts"].items()) or "no open cards"]
    for r in result["cards"]:
        waiting = f" [{len(r['waiting_on_it'])} waiting]" if r["waiting_on_it"] else ""
        lines.append(f"{r['card']:<12} {r['status']:<9} {r['verdict']:<34} {r['title'][:50]}{waiting}")
        if r["next"]:
            lines.append(f"{'':<12} next: {r['next']}")
    return "\n".join(lines)


def _log_text(args) -> str:
    journal = status._load("delivery_journal")
    entries = journal.read(journal.parse_since(getattr(args, "since", None)), getattr(args, "card", None),
                           getattr(args, "kind", None), home=status._home())
    if getattr(args, "json", False):
        return "\n".join(json.dumps(e) for e in entries)
    return "\n".join(journal.format_line(e) for e in entries) or "journal empty for this filter"


def _cli_command(args) -> None:
    # Hermes only uses a handler's return value as the exit code, so print.
    action = getattr(args, "action", "doctor")
    print({"queue": _queue_json, "status": lambda: _status_text(args), "log": lambda: _log_text(args)}.get(
        action, lambda: _doctor_command(args))())


def _workflow_source_status() -> str:
    """Compare the local plugin checkout against origin/main. Never raises."""
    try:
        head = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if head.returncode != 0:
            return "workflow source: unknown (not a git checkout)"
        local = head.stdout.strip()
        remote = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "ls-remote", "origin", "main"],
            capture_output=True, text=True, timeout=5,
        )
        if remote.returncode != 0 or not remote.stdout.strip():
            return f"workflow source: {local[:12]} (update check skipped — offline)"
        remote_sha = remote.stdout.split()[0]
        if local == remote_sha:
            return f"workflow source: {local[:12]} (up to date)"
        return f"workflow source: {local[:12]} (behind origin/main → git pull && ./install.sh)"
    except (OSError, subprocess.TimeoutExpired):
        return "workflow source: unknown (update check skipped — git unavailable)"


def _doctor_command(args) -> str:
    status = [
        f"cpipe plugin: {_REPO_ROOT}",
        f"skills bundled: {len(list((_REPO_ROOT / 'workflow' / 'skills').iterdir()))}",
        f"scripts bundled: {len(list(_SCRIPTS.glob('*.py')))}",
    ]
    policy = subprocess.run(
        [sys.executable, str(_SCRIPTS / "check_delivery_config.py")],
        capture_output=True, text=True, timeout=300,
    )
    status.append(policy.stdout.strip())
    status.extend(liveness.liveness_report())
    status.append(_workflow_source_status())
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
        "description": "Flip one equality condition in a file inside a disposable git worktree, run the focused test, and report whether the test fails. Verifies the test bites. Auto-detects swift/python/node/make stacks; pass test_cmd to override. File is restored after.",
        "parameters": {
            "type": "object",
            "properties": {
                "worktree": {"type": "string", "description": "Absolute path to the disposable git worktree"},
                "file_path": {"type": "string", "description": "Repository-relative file to mutate"},
                "test_filter": {"type": "string", "description": "Test filter expression (e.g. 'TargetTests' or '-k' expression)"},
                "test_cmd": {"type": "string", "description": "Optional test command override, e.g. 'python -m pytest -q'"},
            },
            "required": ["worktree", "file_path", "test_filter"],
        },
    },
}


def register(ctx):
    """Register deterministic delivery tools, doctor CLI, metrics and liveness hooks. The registry wants
    the bare function schema: the OpenAI wrapper made every tool reach the model with no description
    or parameters."""
    ctx.register_tool(
        name="delivery_check_policy", toolset="cpipe",
        schema=_POLICY_SCHEMA["function"], handler=lambda args, **kw: _check_policy(),
    )
    ctx.register_tool(
        name="delivery_board_intelligence", toolset="cpipe",
        schema=_INTEL_SCHEMA["function"], handler=lambda args, **kw: _board_intelligence(),
    )
    ctx.register_tool(
        name="delivery_mutation_check", toolset="cpipe",
        schema=_MUTATION_SCHEMA["function"],
        handler=lambda args, **kw: _mutation_check(
            worktree=args["worktree"], file_path=args["file_path"],
            test_filter=args["test_filter"], test_cmd=args.get("test_cmd")),
    )
    ctx.register_tool(
        name="delivery_submit", toolset="cpipe",
        schema=submit.SCHEMA["function"], handler=submit.submit,
    )
    ctx.register_tool(
        name="delivery_verify_failed", toolset="cpipe",
        schema=submit.VERIFY_FAILED_SCHEMA["function"], handler=submit.verify_failed,
    )
    ctx.register_tool(
        name="delivery_status", toolset="cpipe",
        schema=status.STATUS_SCHEMA["function"], handler=status.delivery_status,
    )
    ctx.register_tool(
        name="delivery_watch", toolset="cpipe",
        schema=status.WATCH_SCHEMA["function"], handler=status.delivery_watch,
    )
    ctx.register_cli_command(
        name="cpipe", help="cpipe plugin doctor",
        setup_fn=_cli_setup, handler_fn=_cli_command,
        description="Check cpipe plugin status and run the policy validator.",
    )
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_hook("on_session_end", exit_handoff.handoff)
    ctx.register_hook("on_kanban_dispatch_tick", liveness.record_dispatch_tick)
    ctx.register_hook("on_kanban_dispatch_tick", block_reasons.explain_blocks)
    ctx.register_hook("pre_llm_call", liveness.liveness_notice)
    ctx.register_hook("pre_llm_call", workspace_prep.prepare_workspace)
    ctx.register_hook("pre_llm_call", status.chat_context)
    ctx.register_hook("transform_tool_result", status.compact_show)
    ctx.register_hook("post_llm_call", status.claim_check)
    ctx.register_hook("pre_approval_request", status.approval_requested)
    ctx.register_hook("pre_tool_call", review_gate.gate)
    ctx.register_hook("pre_tool_call", headless_clarify.gate)
    ctx.register_hook("pre_tool_call", card_gate.gate)
