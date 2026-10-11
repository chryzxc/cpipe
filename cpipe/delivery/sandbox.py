"""Fail-closed, offline macOS process boundary for local delivery checks.

This is NOT a live Hermes executor. Model/network access, profile persistence,
publication and native task dispatch are deliberately unavailable here. A caller
must supply a frozen exact file scope; only disposable runtime scratch is added.
Checks are single-process: forking is denied to prevent detached descendants.
This is a write boundary, not comprehensive host-data isolation: system runtime
reads and filesystem metadata are permitted, and nested secrets need a stronger
mount-based boundary before any untrusted/model-driven workload is enabled.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

from .contracts import relative_path


class SandboxUnavailable(RuntimeError):
    """No conformant local boundary; never retry the command unsandboxed."""


FORBIDDEN = (".git", ".env", ".hermes", ".cpipe")


def _checked(root: Path, relative: str) -> Path:
    relative_path(relative)
    if any(part in FORBIDDEN or part.startswith(".env.") for part in Path(relative).parts):
        raise ValueError("forbidden sandbox path")
    target = root / relative
    # Reject aliases, not just symlinks which happen to remain in the worktree.
    for parent in (target, *target.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("symlink sandbox path")
    if target.exists():
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("sandbox grants require unaliased regular files")
    if target.resolve() != target or not target.parent.is_dir():
        raise ValueError("sandbox grants require canonical paths and existing parents")
    return target


def _filter(kind: str, paths) -> str:
    # JSON quoted strings are valid SBPL strings; never interpolate raw paths.
    return " ".join(f"({kind} {json.dumps(str(p))})" for p in paths)


class Sandbox:
    def __init__(self, worktree, write_paths, *, readonly=False):
        root = Path(worktree).absolute()
        if root.resolve(strict=True) != root or not root.is_dir():
            raise ValueError("sandbox worktree must be a canonical directory")
        if type(readonly) is not bool or type(write_paths) is not tuple:
            raise ValueError("sandbox requires immutable scope and boolean readonly")
        if readonly and write_paths:
            raise ValueError("readonly sandbox cannot accept write grants")
        if len(set(write_paths)) != len(write_paths):
            raise ValueError("duplicate sandbox write grant")
        self.root = root
        self.write_paths = tuple(_checked(root, name) for name in write_paths)
        self.backend = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
        if not self.backend:
            raise SandboxUnavailable("offline macOS sandbox-exec is unavailable")
        self._temp = None
        self.scratch = None

    def __enter__(self):
        if self._temp is not None:
            raise RuntimeError("sandbox already entered")
        # Respect the operator's existing TMPDIR, never write under system /tmp.
        self._temp = tempfile.TemporaryDirectory(prefix="cpipe-sandbox-")
        self.scratch = Path(self._temp.name).resolve()
        return self

    def __exit__(self, *args):
        self._temp.cleanup()
        self._temp = None
        self.scratch = None

    def policy(self):
        if self.scratch is None:
            raise RuntimeError("sandbox must be entered")
        reads = [self.root, self.scratch, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
                 Path("/usr"), Path("/bin"), Path("/sbin"), Path("/System"),
                 Path("/Library/Frameworks"), Path("/private/etc"), Path("/dev")]
        denied = [self.root / name for name in FORBIDDEN]
        denied.extend(p for p in self.root.glob(".env.*"))
        return "\n".join([
            "(version 1)", "(deny default)", "(allow process-exec)",
            # Offline single-process checks cannot fork and detach past cancellation.
            "(deny process-fork)",
            "(allow signal (target self))", "(allow sysctl-read)", "(allow file-read-metadata)",
            '(allow file-read-data (literal "/"))',
            f"(allow file-read-data {_filter('subpath', reads)})",
            f"(deny file-read-data {_filter('subpath', denied)})",
            f"(allow file-write* (subpath {json.dumps(str(self.scratch))}))",
            f"(allow file-write* {_filter('literal', self.write_paths)})" if self.write_paths else "",
        ])

    def run(self, argv, *, timeout):
        if (type(argv) not in {list, tuple} or not argv
                or any(type(v) is not str or not v or "\x00" in v for v in argv)):
            raise ValueError("sandbox requires a literal nonempty argv")
        if type(timeout) not in {float, int} or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("sandbox requires a finite positive timeout")
        for path in self.write_paths:
            _checked(self.root, path.relative_to(self.root).as_posix())
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(self.scratch),
               "TMPDIR": str(self.scratch), "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONNOUSERSITE": "1", "LANG": "en_US.UTF-8"}
        command = [self.backend, "-p", self.policy(), *argv]
        proc = subprocess.Popen(command, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", start_new_session=True)
        try:
            output, _ = proc.communicate(timeout=timeout)
            return subprocess.CompletedProcess(list(argv), proc.returncode, output)
        finally:
            # A forked daemon must not outlive the activity, even after natural exit.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
