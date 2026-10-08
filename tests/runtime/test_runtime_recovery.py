"""Runtime failures never become approval or a false dispatch receipt."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cpipe.runtime import hermes_api as h, launcher, lifecycle
from cpipe.runtime.config import Lane
from cpipe.runtime.result import Status, WorkerResult
from conftest import make_task


def park_startup_failure(board, **kwargs):
    tid = make_task(board, assignee="forge", created_by="cpipe", **kwargs)
    h.comment(board, tid, "hermes", author="cpipe:engine")
    for module in ("yaml", "ruamel"):
        h.kb.block_task(board, tid, reason=f"cpipe.round startup: ModuleNotFoundError: No module named '{module}'", kind="needs_input")
        if module == "yaml":
            assert h.kb.unblock_task(board, tid)
    assert h.get_task(board, tid).status == "triage"
    return tid


def test_repaired_infrastructure_triage_preserves_card_and_scope(board, monkeypatch):
    tid = park_startup_failure(board, title="Preserve scope", body="Original request")
    monkeypatch.setattr(launcher, "preflight", lambda **kw: {"ready": True})
    before = h.get_task(board, tid)
    assert h.resume(board, tid)
    after = h.get_task(board, tid)
    assert after.status == "ready"
    assert (after.title, after.body, after.assignee) == (before.title, before.body, before.assignee)
    assert not any(c.author == "cpipe:approved" for c in h.comments_after(board, tid))


def test_infrastructure_triage_does_not_bypass_parents(board, monkeypatch):
    parent = make_task(board, assignee="forge")
    tid = park_startup_failure(board)
    h.kb.link_tasks(board, parent, tid)
    monkeypatch.setattr(launcher, "preflight", lambda **kw: {"ready": True})
    assert h.resume(board, tid)
    assert h.get_task(board, tid).status == "todo"


def test_unknown_triage_never_resumes(board):
    tid = make_task(board, assignee="forge", created_by="cpipe", triage=True)
    assert not h.resume(board, tid)
    assert h.get_task(board, tid).status == "triage"


def test_failed_runtime_preflight_leaves_triage_intact(board, monkeypatch):
    tid = park_startup_failure(board)
    monkeypatch.setattr(launcher, "preflight", lambda **kw: (_ for _ in ()).throw(RuntimeError("dependency repair failed")))
    with pytest.raises(RuntimeError, match="repair failed"):
        h.resume(board, tid)
    assert h.get_task(board, tid).status == "triage"


def test_failed_probes_consume_bounded_recovery_budget(board, monkeypatch):
    import time
    from cpipe.runtime import recovery
    tid = park_startup_failure(board)
    probes = []
    def fail():
        probes.append(True)
        raise RuntimeError("runtime unavailable")
    monkeypatch.setattr(launcher, "preflight", fail)
    now = time.time()
    task = h.get_task(board, tid)
    assert not recovery.recover(board, task, log=lambda _: None, now=now)
    assert not recovery.recover(board, task, log=lambda _: None, now=now + 1)
    assert not recovery.recover(board, task, log=lambda _: None, now=now + 60)
    assert not recovery.recover(board, task, log=lambda _: None, now=now + 180)
    assert len(probes) == 2
    assert len([c for c in h.comments_after(board, tid) if c.author == recovery.AUTHOR]) == 2
    assert h.get_task(board, tid).status == "triage"


def test_successful_recovery_does_not_approve_or_publish(board, monkeypatch):
    from cpipe.runtime import recovery
    tid = park_startup_failure(board)
    monkeypatch.setattr(launcher, "preflight", lambda: {"ready": True})
    assert recovery.recover(board, h.get_task(board, tid), log=lambda _: None)
    assert h.get_task(board, tid).status == "ready"
    assert not any(c.author in ("cpipe:accept", "cpipe:ship") for c in h.comments_after(board, tid))


def test_module_error_in_product_question_is_not_startup_recovery(board):
    from cpipe.runtime import recovery
    tid = make_task(board, assignee="forge", created_by="cpipe")
    h.kb.block_task(board, tid, reason="Approve scope to fix ModuleNotFoundError: No module named 'yaml'", kind="needs_input")
    assert recovery.eligible(board, h.get_task(board, tid)) is None


def test_paused_round_receipt_resumes_but_stale_receipt_does_not(board, monkeypatch):
    from cpipe.runtime import recovery
    tid = make_task(board, assignee="forge", created_by="cpipe")
    task = h.claim_task(board, tid, claimer=h.new_claimer(), ttl_seconds=60)
    assert lifecycle.apply(board, lane=Lane("hermes", "forge", "round"), task_id=tid,
                           run_id=task.current_run_id,
                           result=WorkerResult(Status.BLOCKED, "Paused at local budget",
                                               {"cpipe": {"node": "paused", "runtime_failure": "transient"}}),
                           pid_tracked=False, log=print)
    assert recovery.eligible(board, h.get_task(board, tid)) == "transient:paused"
    monkeypatch.setattr(launcher, "preflight", lambda: {"ready": True})
    assert recovery.recover(board, h.get_task(board, tid), log=print)
    task = h.claim_task(board, tid, claimer=h.new_claimer(), ttl_seconds=60)
    assert h.block(board, tid, run_id=task.current_run_id, reason="Choose the new product scope", kind="needs_input")
    assert recovery.eligible(board, h.get_task(board, tid)) is None


def test_failure_block_policy_is_infrastructure_not_a_question(board):
    tid = make_task(board, assignee="cp:test")
    task = h.claim_task(board, tid, claimer=h.new_claimer(), ttl_seconds=60)
    lifecycle.apply(board, lane=Lane("test", "cp:test", "command", on_failure="block"), task_id=tid,
                    run_id=task.current_run_id, result=WorkerResult(Status.FAILED, "startup failed"),
                    pid_tracked=False, log=print)
    assert h.get_task(board, tid).block_kind == "transient"


def test_real_bootstrap_launch_ignores_ambient_pythonpath(tmp_path, monkeypatch):
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "yaml.py").write_text("raise RuntimeError('project shadow imported')")
    import os
    from conftest import REAL_RUNTIME_COMMAND, _HERMES
    from hermes_cli import _launchers
    monkeypatch.setattr(_launchers, "runtime_command", REAL_RUNTIME_COMMAND)
    monkeypatch.setenv("HERMES_HOME", str(_HERMES.parent))
    env = {**os.environ, "PYTHONPATH": str(shadow)}
    done = subprocess.run(launcher.module_command("cpipe.runtime.preflight"), env=env, cwd=shadow,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    receipt = json.loads(done.stdout.splitlines()[-1])
    assert receipt["ready"] is True
    assert receipt["package"] == str(Path(__file__).resolve().parents[2])
    assert receipt["python"]


def test_failed_probe_collects_dependencies_without_worktree(tmp_path, monkeypatch):
    from cpipe.runtime import preflight
    def missing(name):
        if name in ("yaml", "ruamel.yaml"):
            raise ModuleNotFoundError(name)
        return object()
    monkeypatch.setattr(preflight.importlib, "import_module", missing)
    before = list(tmp_path.iterdir())
    receipt = preflight.check()
    assert receipt["ready"] is False
    assert {f["module"] for f in receipt["failures"]} >= {"yaml", "ruamel.yaml"}
    assert list(tmp_path.iterdir()) == before
