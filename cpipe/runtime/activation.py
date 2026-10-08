"""Bounded daemon readiness for explicit commands; registration is read-only."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import paths
from .launcher import module_command, preflight


def health_path() -> Path:
    return paths.runtime_home() / "daemon-health.json"


def publish_health():
    from .scheduler import _package_root
    record = {"pid": os.getpid(), "updated": time.time(), "package": _package_root()}
    target = health_path()
    temp = target.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(record))
    temp.replace(target)


def readiness():
    from .scheduler import _pid_alive, _package_root
    try:
        data = json.loads(health_path().read_text())
        if (data["package"] == _package_root() and time.time() - data["updated"] < 15
                and _pid_alive(int(data["pid"]))):
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def ensure_running(timeout: float = 5):
    """Only called after an explicit delivery action. Never silently queue work."""
    from .config import load_config
    from .scheduler import _hermes_root, _package_root
    from .dispatch_guard import install
    install()
    load_config()  # validate before spawning
    receipt = preflight()  # same bootstrap and plugin root as every worker
    ready = readiness()
    if ready:
        return {**ready, "preflight": receipt}
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (_package_root(), _hermes_root(), env.get("PYTHONPATH", "")) if p)
    with (paths.runtime_home() / "daemon.log").open("a") as log:
        proc = subprocess.Popen(module_command("cpipe.runtime", "run"), env=env,
                                start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=log, close_fds=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready := readiness():
            return {**ready, "preflight": receipt}
        if proc.poll() is not None:
            break
        time.sleep(.05)  # launch handshake only, never polling a delivery
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    raise RuntimeError("cpipe runner did not become ready; no work was queued. Check hermes cpipe doctor.")


def nudge():
    """One immediate claim pass, not a wait loop; CAS arbitrates the daemon race."""
    from .config import load_config
    from .scheduler import Scheduler
    return Scheduler(load_config()).tick()
