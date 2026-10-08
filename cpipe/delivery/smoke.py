"""Offline fixture exercise of the v2 reducer, journal and real OS boundary.

No native tasks, model calls, user repositories, independent reviews or publication.
A successful run is a testable fixture, NOT a ready live delivery.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any

from .contracts import Budget, Envelope, Outcome
from .journal import Journal
from .observations import Observation, Proof, to_receipt
from .sandbox import Sandbox
from .schema import Actor, CheckIdentity, ExecutionPlan
from .service import Event, Service


CHECK = "from double import double; assert double(9) == 18; assert double(-3) == -6; print('FIXTURE_BEHAVIOR_PASS')"
BUILD = "from pathlib import Path; Path('double.py').write_text('def double(n): return n * 2\\n')"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def run() -> dict:
    # Explicit dir avoids tempfile's cached default ignoring a changed TMPDIR.
    with tempfile.TemporaryDirectory(prefix="cpipe-v2-smoke-", dir=os.environ.get("TMPDIR")) as directory:
        scratch = Path(directory).resolve()
        root = scratch / "fixture"
        root.mkdir()
        source = root / "double.py"
        source.write_text("def double(n): return n\n")
        outside = scratch / "outside.txt"
        outside.write_text("unchanged")
        git_env = {"PATH": "/usr/bin:/bin", "HOME": str(scratch),
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}

        def git(*args):
            return subprocess.run(["/usr/bin/git", *args], cwd=root, env=git_env,
                                  stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, check=True, timeout=10).stdout.strip()

        git("init", "--initial-branch=fixture")
        git("add", "double.py")
        git("-c", "user.name=Offline fixture", "-c", "user.email=fixture@invalid",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
            "commit", "-m", "test: create disposable smoke fixture")
        base = git("rev-parse", "HEAD")
        config_before = (root / ".git/config").read_bytes()
        delivery = "smoke-" + uuid.uuid4().hex
        now = int(time.time())
        argv = (sys.executable, "-c", CHECK)
        check_id = digest(json.dumps(argv).encode())
        environment = digest((sys.executable + sys.version + sys.platform).encode())
        envelope = Envelope(delivery, delivery, "offline-smoke", "local-fixture", "fixture",
                            base, "double.py", ("behavior", "review"), frozenset({"local"}))
        plan = ExecutionPlan(
            envelope, str(root), str(root / ".git"), base,
            "Exercise double(n) behavior in a disposable offline fixture",
            ("double(9) == 18", "double(-3) == -6"),
            ("No native workers", "No model calls", "No publication", "No readiness review"),
            ("double.py",), (".git", ".env", ".hermes", ".cpipe"),
            (Actor("builder", "cli", "fixture-builder", "none", "none"),
             Actor("reviewer", "cli", "fixture-reviewer", "none", "none")),
            Budget(60, 2), "offline-macos-fixture", check_id,
            (CheckIdentity("behavior", check_id, environment, check_id),
             CheckIdentity("review", "not-run", environment, "not-run")),
            True, False, 0, "local_only", now + 60)
        artifact_before = digest(source.read_bytes())
        journal_path = scratch / "facts.db"
        with Journal(journal_path) as journal:
            service = Service(journal)
            state = None
            last_event = None

            def accept(kind, **changes):
                nonlocal state, last_event
                fields: dict[str, Any] = dict(key="event-" + uuid.uuid4().hex, kind=kind, delivery_id=delivery,
                              plan_digest=plan.digest(), artifact=state.artifact if state else artifact_before,
                              expected_version=state.version if state else 0, now=int(time.time()),
                              session_id=delivery, profile_id="offline-smoke",
                              activity_id=state.active_intent if state else "",
                              run_id=state.run_id if state else "",
                              worker_session=state.worker_session if state else "")
                fields.update(changes)
                last_event = Event(**fields)
                state, _ = service.accept(last_event)

            def claim(actor="fixture-controller", session=""):
                accept("activity_claimed", actor_id=actor,
                       worker_session=session, run_id="run-" + uuid.uuid4().hex)

            def check():
                before = digest(source.read_bytes())
                with Sandbox(root, (), readonly=True) as boundary:
                    result = boundary.run(argv, timeout=10)
                after = digest(source.read_bytes())
                proof = None
                if result.returncode == 0 and result.stdout.strip() == "FIXTURE_BEHAVIOR_PASS":
                    proof = Proof("behavior", Outcome.PASS, "criterion", check_id)
                elif result.returncode == 1 and "AssertionError" in result.stdout:
                    proof = Proof("behavior", Outcome.FAIL, "assertion", check_id)
                observation = Observation(before, after, argv, str(root), result.returncode,
                                          digest(result.stdout.encode()), "evidence/behavior.txt",
                                          state.run_id, "fixture-controller", proof=proof)
                receipt = to_receipt(observation, "behavior", envelope.scope, check_id, environment, check_id)
                accept("activity_observed", receipts=(receipt,))
                return result

            accept("proposal_created", plan=plan)
            # Only approves this generated fixture, never native/model authority.
            accept("envelope_approved")
            claim()
            red = check()
            if state.activity != "implement":
                raise RuntimeError("offline fixture did not produce an observed behavioral RED")
            claim("fixture-builder", "fixture-builder-session")
            with Sandbox(root, plan.write_paths) as boundary:
                built = boundary.run((sys.executable, "-c", BUILD), timeout=10)
                denied = {}
                for name, command in (
                    ("outside_write_denied", "from pathlib import Path; Path('../outside.txt').write_text('bad')"),
                    ("git_write_denied", "from pathlib import Path; Path('.git/config').write_text('bad')"),
                    ("network_denied", "import socket; socket.socket().bind(('127.0.0.1', 0))"),
                ):
                    probe = boundary.run((sys.executable, "-c", command), timeout=10)
                    denied[name] = probe.returncode != 0 and "PermissionError" in probe.stdout
            if built.returncode != 0 or not all(denied.values()):
                raise RuntimeError("offline fixture write boundary failed")
            if outside.read_text() != "unchanged" or (root / ".git/config").read_bytes() != config_before:
                raise RuntimeError("offline fixture escaped its write scope")
            artifact_after = digest(source.read_bytes())
            accept("activity_observed", actor_id="fixture-builder", result_artifact=artifact_after)
            claim()
            green = check()
            if state.phase != "testable":
                raise RuntimeError("offline fixture did not reach a testable candidate")
            version = state.version
            replay, _ = service.accept(last_event)
            replay_verified = replay == state and service.read(delivery).version == version
        with Journal(journal_path) as reopened:
            journal_reopened = Service(reopened).read(delivery) == state
        report = dict(mode="offline_fixture", phase=state.phase,
                      live_workers_enabled=False, publication_enabled=False,
                      native_tasks_created=0, model_calls=0, review_performed=False,
                      journal_reopened=journal_reopened, event_replay_verified=replay_verified,
                      red_exit_code=red.returncode, green_exit_code=green.returncode,
                      artifact_before=artifact_before, artifact_after=artifact_after, **denied)
    report["scratch_cleaned"] = not scratch.exists()
    if not all(report[name] for name in ("journal_reopened", "event_replay_verified", "scratch_cleaned")):
        raise RuntimeError("offline fixture persistence or cleanup verification failed")
    return report
