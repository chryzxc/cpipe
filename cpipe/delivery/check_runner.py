"""Trusted, local check observation adapter; no model-authored green receipts.

A check is the repository's own test command (any language), run in the
worktree. The approved argv and criteria are frozen. Outcomes come from a JUnit
report when the argv names ``{report}``, else from the exit code plus the
criteria appearing in the output. Report and output stay in memory or a private
temporary directory: raw assertion text, credentials and captured output never
enter an evidence file or journal. Saved evidence contains only criterion
identity hashes, outcomes and ``[REDACTED]`` detail markers.

This adapter observes an unsandboxed subprocess. Before/after source fingerprints
are regression fences, not prevention of arbitrary filesystem/network access.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

from .artifact import fingerprint, validate_worktree
from .contracts import Outcome, Receipt, identity, string_tuple
from .journal import encoded
from .schema import CheckIdentity

MAX_REPORT = 2 * 1024 * 1024
REPORT = '{report}'
# Shell conventions: cannot execute / not found. Never behavioral RED.
_NOT_RUN = {126, 127}


def _digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def _environment(program):
    # Checks deliberately do not inherit arbitrary settings or credentials.
    # Hash only the safe, explicitly selected subprocess definition. Secret
    # values must never enter persisted identities, even through a hash.
    # ponytail: the resolved program is hashed, not the project's installed
    # dependencies; lockfiles are already in the artifact fingerprint.
    from ..runtime.envpolicy import Redactor
    safe = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR')
    redact = Redactor(os.environ)
    env = {name: os.environ[name] for name in safe if name in os.environ
           and redact(os.environ[name]) == os.environ[name]}
    env['CI'] = '1'  # Non-interactive: no watch modes or prompts.
    path = program if os.path.isabs(program) else shutil.which(program, path=env.get('PATH'))
    if not path or not os.access(path, os.X_OK):
        raise ValueError('check program is not installed or not on PATH')
    return env, _digest([os.path.realpath(path), sorted(env.items())])


@dataclass(frozen=True)
class CommandCheck:
    obligation: str
    argv: tuple[str, ...]
    criteria: tuple[str, ...] = ()
    timeout_seconds: int = 120

    def __post_init__(self):
        identity(self.obligation, 'obligation')
        if type(self.argv) is not tuple or not self.argv:
            raise ValueError('check command must be a non-empty argv list')
        for arg in self.argv:
            if type(arg) is not str or '\0' in arg:
                raise ValueError('invalid check command')
        identity(self.argv[0], 'check program')
        if '/' in self.argv[0] and not os.path.isabs(self.argv[0]):
            raise ValueError('check program must be on PATH or an absolute path')
        if sum(REPORT in arg for arg in self.argv) > 1:
            raise ValueError('check command may name {report} once')
        string_tuple(self.criteria, 'check criteria')
        if type(self.timeout_seconds) is not int or not 0 < self.timeout_seconds <= 1800:
            raise ValueError('invalid check timeout')

    @property
    def reported(self):
        return any(REPORT in arg for arg in self.argv)

    def command(self, report):
        return [arg.replace(REPORT, report) for arg in self.argv]

    def identity(self):
        return CheckIdentity(self.obligation, _digest([self.argv, self.criteria]),
            _environment(self.argv[0])[1], _digest([self.argv, self.timeout_seconds]))


def _run(spec, worktree, env, timeout):
    """Return (exit code, output tail, report) or None when the run was not observed."""
    output = bytearray()
    process = None
    with tempfile.TemporaryDirectory(prefix='cpipe-check-') as private:
        report = os.path.join(private, 'report.xml')
        try:
            process = subprocess.Popen(spec.command(report), cwd=worktree, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True)
            fd = process.stdout.fileno()
            os.set_blocking(fd, False)
            deadline = time.monotonic() + timeout
            while True:
                if time.monotonic() >= deadline:
                    return None
                readable, _, _ = select.select([fd], [], [], min(.1, max(0, deadline - time.monotonic())))
                if readable:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    output.extend(chunk)
                    del output[:-MAX_REPORT]  # Keep the tail; criteria are searched in memory only.
                elif process.poll() is not None:
                    break
            code = process.wait(timeout=max(.1, deadline - time.monotonic()))
            raw = None
            if spec.reported and os.path.isfile(report) and os.path.getsize(report) <= MAX_REPORT:
                raw = Path(report).read_bytes()
            return code, bytes(output), raw
        except (OSError, subprocess.TimeoutExpired):
            return None
        finally:
            if process is not None:
                # Terminate any same-group descendants too; escaping descendants
                # are outside this observer's boundary (no confinement is claimed).
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):  # macOS: EPERM once only zombies remain
                    pass
                process.wait(timeout=5)
                process.stdout.close()
            output.clear()


# "RuntimeError: ...", "TypeError: ..." are harness/runtime failures, not behavioral
# RED. Assertion messages ("assert ...", "AssertionError", jest's bare "Error: expect(...)") are.
# ponytail: only runners that lead the message with the exception type (pytest, vitest, jest)
# are told apart; node:test hides it in the trace, so its crashes count as RED.
_EXCEPTION = re.compile(r'([\w.]*?)(?:Error|Exception)\b')


def _harness(failure):
    # vitest/jest put the error class in type=; pytest leads the message with it.
    kind = failure.attrib.get('type') or failure.attrib.get('message') or (failure.text or '').strip()
    match = _EXCEPTION.match(kind)
    return bool(match) and match.group(1).rsplit('.', 1)[-1] not in {'', 'Assertion'}


def _named(case, criterion):
    name = case.attrib['name']
    # vitest ("describe > test") and jest ("describe test") prefix describe titles.
    return criterion in (name, f"{case.attrib.get('classname', '')}::{name}") or name.endswith(' ' + criterion)


def _junit(spec, code, raw):
    """(outcome, reason); reason is a fixed label, never report text."""
    if not raw or b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
        return Outcome.UNAVAILABLE, 'no_report'
    try:
        root = ET.fromstring(raw)
        if root.tag not in {'testsuites', 'testsuite'}:
            return Outcome.UNAVAILABLE, 'no_report'
        cases = list(root.iter('testcase'))
        if not cases:
            return Outcome.UNAVAILABLE, 'no_test_cases'
        # A criterion names exactly one reported case. Missing, duplicate or
        # skipped criteria cannot stand in for a behavioral test.
        watched = []
        for criterion in spec.criteria:
            hits = [c for c in cases if _named(c, criterion)]
            if len(hits) != 1:
                return Outcome.UNAVAILABLE, 'criterion_not_found' if not hits else 'criterion_ambiguous'
            watched.append(hits[0])
        watched = watched or cases
        if any(c.find('skipped') is not None for c in watched):
            return Outcome.UNAVAILABLE, 'test_skipped'
        if any(c.find('error') is not None for c in watched):
            return Outcome.UNAVAILABLE, 'test_crashed'
        mine = [c.find('failure') for c in watched if c.find('failure') is not None]
        if any(_harness(f) for f in mine):
            return Outcome.UNAVAILABLE, 'test_crashed'
        if code == 0 and not any(c.find('failure') is not None for c in cases):
            return Outcome.PASS, ''
        if code != 0 and mine:
            return Outcome.FAIL, ''
        return Outcome.UNAVAILABLE, 'other_failures'
    except (ET.ParseError, KeyError, ValueError, TypeError):
        return Outcome.UNAVAILABLE, 'no_report'


def _outcome(spec, observed):
    """(outcome, reason); see contracts.UNAVAILABLE_REASONS."""
    if observed is None:
        return Outcome.UNAVAILABLE, 'timed_out'
    code, output, raw = observed
    if code in _NOT_RUN:
        return Outcome.UNAVAILABLE, 'command_not_found'
    if code < 0:
        return Outcome.UNAVAILABLE, 'killed'
    if spec.reported:
        return _junit(spec, code, raw)
    # Exit code alone cannot tell a failing test from one that never ran
    # (compile/import error), so every criterion must appear in the output.
    if not all(c.encode() in output for c in spec.criteria):
        return Outcome.UNAVAILABLE, 'criteria_not_in_output'
    return (Outcome.PASS if code == 0 else Outcome.FAIL), ''


def observe_check(state, spec, *, evidence_root):
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
    env, environment = _environment(spec.argv[0])
    if environment != definition.environment:
        raise ValueError('environment changed before check execution')
    observed = _run(spec, state.plan.worktree, env, min(spec.timeout_seconds, remaining))
    after = fingerprint(state.plan.worktree)
    if after != before:
        raise ValueError('artifact changed during read-only check')
    if _environment(spec.argv[0])[1] != environment:
        raise ValueError('environment changed during check execution')
    outcome, reason = _outcome(spec, observed)
    code = observed[0] if observed else None
    observed = None  # Never persist report text or test output.
    receipt = Receipt(spec.obligation, before.digest, state.plan.envelope.scope,
                      definition.inputs, environment, definition.command, outcome,
                      outcome in {Outcome.PASS, Outcome.FAIL}, reason)
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
