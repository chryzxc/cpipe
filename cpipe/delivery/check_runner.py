"""Trusted, local pytest observation adapter; no model-authored green receipts.

The approved command and exact criterion IDs are frozen. Pytest's JUnit report
travels through a bounded in-memory pipe: raw assertion text, credentials and
captured output never enter an evidence file or journal. Saved evidence contains
only approved criterion identity hashes, outcomes and ``[REDACTED]`` detail markers.

This adapter observes an unsandboxed subprocess. Before/after source fingerprints
are regression fences, not prevention of arbitrary filesystem/network access.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from .artifact import fingerprint, validate_worktree
from .contracts import Outcome, Receipt, identity, relative_path, string_tuple
from .journal import encoded
from .schema import CheckIdentity

MAX_REPORT = 2 * 1024 * 1024


def _digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def _environment():
    # Checks deliberately do not inherit arbitrary settings or credentials.
    # Hash only the safe, explicitly selected subprocess definition. Secret
    # values must never enter persisted identities, even through a hash.
    from ..runtime.envpolicy import Redactor
    safe = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR')
    redact = Redactor(os.environ)
    env = {name: os.environ[name] for name in safe if name in os.environ
           and redact(os.environ[name]) == os.environ[name]}
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTEST_ADDOPTS='')
    packages = sorted({(d.metadata['Name'], d.version) for d in importlib.metadata.distributions()})
    return env, _digest([sys.executable, sys.prefix, sys.version, sys.platform,
                         packages, sorted(env.items())])


@dataclass(frozen=True)
class PytestCheck:
    obligation: str
    targets: tuple[str, ...]
    criteria: tuple[str, ...]
    timeout_seconds: int = 120

    def __post_init__(self):
        identity(self.obligation, 'obligation')
        string_tuple(self.targets, 'pytest targets')
        string_tuple(self.criteria, 'pytest criteria')
        if not self.targets or not self.criteria:
            raise ValueError('exact test targets and criteria are required')
        for target in self.targets:
            relative_path(target)
            if not target.endswith('.py') or target.startswith('-'):
                raise ValueError('pytest targets must be explicit relative Python files')
        if type(self.timeout_seconds) is not int or not 0 < self.timeout_seconds <= 600:
            raise ValueError('invalid pytest timeout')

    def argv(self, report):
        return [sys.executable, '-m', 'pytest', *self.targets, '-q', '-o', 'addopts=',
                '-o', 'junit_family=xunit2', f'--junitxml={report}']

    def identity(self):
        return CheckIdentity(self.obligation, _digest([self.targets, self.criteria]),
            _environment()[1], _digest([self.argv('<private-report-pipe>'), self.timeout_seconds]))


def _run(spec, worktree, env, timeout):
    # /dev/fd is supported on the macOS/Linux native runtimes targeted here.
    # No shell, raw report file, inherited stdin, or raw process output log.
    if os.name != 'posix' or not Path('/dev/fd').is_dir():
        return None, None
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    process = None
    data = bytearray()
    oversized = False
    timed_out = False
    try:
        process = subprocess.Popen(spec.argv(f'/dev/fd/{write_fd}'), cwd=worktree, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(write_fd,), start_new_session=True)
        os.close(write_fd)
        write_fd = None
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            readable, _, _ = select.select([read_fd], [], [], min(.1, max(0, deadline - time.monotonic())))
            if readable:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                if len(data) + len(chunk) > MAX_REPORT:
                    oversized = True
                    data.clear()
                elif not oversized:
                    data.extend(chunk)
            elif process.poll() is not None:
                break
        if timed_out:
            os.killpg(process.pid, signal.SIGKILL)
        code = process.wait(timeout=max(.1, deadline - time.monotonic()) if not timed_out else 5)
        return (None, None) if timed_out or oversized else (code, bytes(data))
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    finally:
        if process is not None:
            # Terminate any same-group descendants too; escaping descendants
            # are outside this observer's boundary (no confinement is claimed).
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        data.clear()


def _outcome(spec, code, raw):
    if code not in {0, 1} or not raw or b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
        return Outcome.UNAVAILABLE
    try:
        root = ET.fromstring(raw)
        if root.tag not in {'testsuites', 'testsuite'}:
            return Outcome.UNAVAILABLE
        suites = list(root.iter('testsuite'))
        cases = list(root.iter('testcase'))
        if not suites or not cases:
            return Outcome.UNAVAILABLE
        if sum(int(s.attrib['tests']) for s in suites) != len(cases):
            return Outcome.UNAVAILABLE
        names = [f"{c.attrib['classname']}::{c.attrib['name']}" for c in cases]
        # Exact expected coverage; duplicate, skipped, extra or missing criteria
        # cannot stand in for an approved behavioral test.
        if len(set(names)) != len(names) or set(names) != set(spec.criteria):
            return Outcome.UNAVAILABLE
        failures = []
        for case in cases:
            if case.find('skipped') is not None or case.find('error') is not None:
                return Outcome.UNAVAILABLE
            failure = case.find('failure')
            if failure is not None:
                message = failure.attrib.get('message', '')
                # Harness/runtime exceptions are unavailable, not behavioral RED.
                if not (message.startswith('assert ') or message == 'AssertionError'
                        or message.startswith('AssertionError:')):
                    return Outcome.UNAVAILABLE
                failures.append(failure)
        if code == 0 and not failures:
            return Outcome.PASS
        if code == 1 and failures:
            return Outcome.FAIL
    except (ET.ParseError, KeyError, ValueError, TypeError):
        pass
    return Outcome.UNAVAILABLE


def observe_pytest(state, spec, *, evidence_root):
    if not state.approved:
        raise ValueError('check requires approved execution authority')
    if state.activity not in {'tests', 'checks'}:
        raise ValueError('not a check activity')
    now = time.time()
    remaining = min(state.plan.expires_at - now,
                    state.plan.budget.seconds - (now - state.started_at))
    if remaining <= 0:
        raise ValueError('execution authority expired or budget exhausted')
    definition = spec.identity()
    if definition not in state.plan.checks:
        raise ValueError('check definition differs from frozen plan')
    before = validate_worktree(state.plan)
    if before.digest != state.artifact:
        raise ValueError('artifact differs from scheduled revision')
    root = Path(evidence_root).absolute()
    if root.resolve() != root or root.is_relative_to(Path(state.plan.worktree)):
        raise ValueError('evidence must be outside source in a non-symlink directory')
    env, environment = _environment()
    if environment != definition.environment:
        raise ValueError('environment changed before check execution')
    code, raw = _run(spec, state.plan.worktree, env, min(spec.timeout_seconds, remaining))
    after = fingerprint(state.plan.worktree)
    if after != before:
        raise ValueError('artifact changed during read-only check')
    if _environment()[1] != environment:
        raise ValueError('environment changed during check execution')
    outcome = _outcome(spec, code, raw)
    raw = None  # Never persist report text or test output.
    receipt = Receipt(spec.obligation, before.digest, state.plan.envelope.scope,
                      definition.inputs, environment, definition.command, outcome,
                      outcome in {Outcome.PASS, Outcome.FAIL})
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Parameterized IDs can embed credentials even in an approved criterion.
    # Persist their identity hashes, never the raw criterion strings.
    evidence = {'schema': 1, 'receipt': receipt.to_dict(),
                'criteria_fingerprints': [_digest(c) for c in spec.criteria],
                'exit_code': code, 'details': '[REDACTED]'}
    fd = os.open(root / f'{uuid.uuid4().hex}.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(encoded(evidence))
    return receipt
