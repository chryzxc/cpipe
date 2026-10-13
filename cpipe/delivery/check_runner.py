"""Trusted, local check observation adapter; no model-authored green receipts.

A check is the repository's own test command (any language), run in the
worktree. The approved argv and criteria are frozen. Outcomes come from a JUnit
(or jest --json) report when the argv names ``{report}``, else from the exit code plus the
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
import json
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
from .service import deadline

MAX_REPORT = 2 * 1024 * 1024
REPORT = '{report}'
# Shell conventions: cannot execute / not found. Never behavioral RED.
_NOT_RUN = {126, 127}


def _digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


SAFE_ENV = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR')


def frozen_environment():
    """The safe subprocess settings of this process, frozen into the plan at approval so every later process
    (another chat, a restarted app, the watchdog) runs the checks the same way."""
    # Checks deliberately do not inherit arbitrary settings or credentials.
    # Secret values must never be persisted, even through a hash.
    from ..runtime.envpolicy import Redactor
    redact = Redactor(os.environ)
    return tuple(sorted((name, os.environ[name]) for name in SAFE_ENV
                        if name in os.environ and redact(os.environ[name]) == os.environ[name]))


def _environment(program, frozen):
    # The resolved program is hashed, not the project's installed dependencies;
    # lockfiles are already in the artifact fingerprint.
    env = dict(frozen)
    env['CI'] = '1'  # Non-interactive: no watch modes or prompts.
    path = program if os.path.isabs(program) else shutil.which(program, path=env.get('PATH'))
    if not path or not os.access(path, os.X_OK):
        raise ValueError(f'check program {program!r} is not installed or not on the frozen PATH')
    return env, _digest([path, sorted(env.items())])


@dataclass(frozen=True)
class CommandCheck:
    obligation: str
    argv: tuple[str, ...]
    criteria: tuple[str, ...] = ()
    timeout_seconds: int = 120
    env: tuple[tuple[str, str], ...] = ()  # frozen at approval; empty means this process's settings
    pinned: str = ''  # the approved environment identity of a plan frozen before environments were stored

    def __post_init__(self):
        if not self.env:
            object.__setattr__(self, 'env', frozen_environment())
        if (type(self.env) is not tuple or any(type(p) is not tuple or len(p) != 2 or p[0] not in SAFE_ENV
                                               or type(p[1]) is not str for p in self.env)):
            raise ValueError('invalid frozen check environment')
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

    def environment(self):
        return _environment(self.argv[0], self.env)

    def identity(self):
        return CheckIdentity(self.obligation, _digest([self.argv, self.criteria]),
            self.pinned or self.environment()[1], _digest([self.argv, self.timeout_seconds]))


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
# Only runners that lead the message with the exception type (pytest, vitest, jest)
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


def _jest(raw):
    """jest --json --outputFile report as JUnit, so jest needs no reporter package. A suite
    that failed to load has no assertions: it becomes one crashed case named after its file."""
    root = ET.Element('testsuites')
    for suite in json.loads(raw)['testResults']:
        cases = suite.get('assertionResults') or []
        for result in cases:
            case = ET.SubElement(root, 'testcase', classname=suite['name'], name=result['fullName'])
            if result['status'] == 'failed':
                ET.SubElement(case, 'failure').text = (result.get('failureMessages') or [''])[0]
            elif result['status'] != 'passed':
                ET.SubElement(case, 'skipped')
        if not cases and suite.get('status') == 'failed':
            ET.SubElement(ET.SubElement(root, 'testcase', classname=suite['name'], name=suite['name']), 'error')
    return ET.tostring(root)


def _junit(spec, code, raw):
    """(outcome, reason); reason is a fixed label, never report text."""
    if not raw or b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
        return Outcome.UNAVAILABLE, 'no_report'
    try:
        root = ET.fromstring(_jest(raw) if raw.lstrip().startswith(b'{') else raw)
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
    except (ET.ParseError, KeyError, ValueError, TypeError, AttributeError):
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
    # Exit code alone cannot tell a failing test from one that never ran (compile/import error),
    # so the behavior check's criteria must appear in the output. Any other check guards what
    # already exists: its exit code, against the baseline's failures, is the answer, whatever
    # its criteria say.
    if spec.obligation == 'behavior' and not all(c.encode() in output for c in spec.criteria):
        return Outcome.UNAVAILABLE, 'criteria_not_in_output'
    return (Outcome.PASS if code == 0 else Outcome.FAIL), ''


# Redacted output tail and missing criteria per check that did not pass, for the builder's
# next turn only (like review.NOTES): process memory, never journal or evidence, so the
# builder fixes from what the run printed instead of re-running and re-exploring.
TAILS = {}
TAIL = 3000


def _remember(state, spec, observed, outcome, known=()):
    key = state.plan.envelope.delivery_id + ':' + spec.obligation
    if outcome is Outcome.PASS or observed is None:
        TAILS.pop(key, None)
        return
    from .review import redacted
    if len(TAILS) > 500:
        TAILS.clear()
    _, output, raw = observed
    seen = output + (raw or b'')
    missing = [c for c in spec.criteria if c.encode() not in seen]
    tail = output[-TAIL:].decode(errors='replace')
    # The failing test names and assertions, wherever the runner printed them (often far above the tail),
    # split from what already failed before the change, so the builder fixes only its own failures.
    failed = [line.rstrip() for line in output.splitlines() if _FAILED.search(line) and line.strip()]
    new = [line for line in failed if _mark(line) not in known]
    if new:
        lines = b'\n'.join(dict.fromkeys(new))[:TAIL].decode(errors='replace')
        old = len(failed) - len(new)
        tail = ('Failure lines new since the change:\n' + lines
                + (f'\n({old} more failure lines also failed before the change: not yours, leave them)' if old else '')
                + '\n\nEnd of the output:\n' + tail)
    TAILS[key] = (state.artifact, [redacted(c, 200) for c in missing], redacted(tail, 2 * TAIL + 200))


def _marks(spec, observed):
    """Hashes of what failed: JUnit case IDs, else the output's lines with numbers
    masked (timings, counts). Never the text itself."""
    code, output, raw = observed
    if spec.reported:
        try:
            cases = ET.fromstring(raw).iter('testcase') if raw and b'<!DOCTYPE' not in raw and b'<!ENTITY' not in raw else ()
            return {_digest(f"{c.attrib.get('classname', '')}::{c.attrib.get('name', '')}") for c in cases
                    if c.find('failure') is not None or c.find('error') is not None}
        except ET.ParseError:
            return set()
    # Only lines that report a failure, numbers and ids masked: new tests that pass print new lines too, and must not make an
    # old failure look new. A new error matching an old line except for its numbers is missed; JUnit
    # reports have no such gap.
    return {_mark(line) for line in output.splitlines() if _FAILED.search(line) and line.strip()}


def _mark(line):
    return _digest(re.sub(rb'[0-9a-f]{8,}|\d+', b'#', line.strip()).decode(errors='replace'))


# How common runners (pytest, jest, vitest, mocha, go, cargo, unittest) mark a failing test or an error.
_FAILED = re.compile(rb'fail|error|exception|traceback|panic|assert|expected|\u2715|\u2716|\u2717|\u2718|\u00d7|\u25cf'
                     .decode('unicode_escape').encode(), re.I)


def baseline_path(root, state):
    return Path(root).absolute() / 'baseline' / f'{state.plan.envelope.delivery_id}.json'


def take_baseline(state, specs, *, evidence_root):
    """Record each check's failures on the code before the change (see hermes_worker.at_base), so
    failures the repository already had (other worktrees, someone's unrelated tests) never block the run."""
    path = baseline_path(evidence_root, state)
    before = fingerprint(state.plan.worktree)
    known = {}
    for spec in specs:
        observed = _run(spec, state.plan.worktree, spec.environment()[0], spec.timeout_seconds)
        if observed and observed[0] > 0 and observed[0] not in _NOT_RUN:
            known[spec.obligation] = sorted(_marks(spec, observed))
        observed = None
    if fingerprint(state.plan.worktree) != before:
        raise ValueError('artifact changed during read-only check')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(encoded(known))
    os.replace(temp, path)


def _known(root, state, spec):
    try:
        return set(json.loads(baseline_path(root, state).read_text()).get(spec.obligation) or ())
    except (OSError, ValueError, AttributeError):
        return set()


def _pre_existing(root, state, spec, observed, outcome, reason):
    """Failures all already there before the change, outside the change's own tests: the behavior
    check's criteria must still pass (reported) or appear in no failure line (output only)."""
    if not observed or observed[0] <= 0 or not (reason == 'other_failures' or (outcome is Outcome.FAIL and not spec.reported)):
        return False
    if spec.obligation == 'behavior' and reason != 'other_failures' and any(
            c.encode() in line for line in observed[1].splitlines() if _FAILED.search(line) for c in spec.criteria):
        return False
    try:
        known = json.loads(baseline_path(root, state).read_text()).get(spec.obligation)
    except (OSError, ValueError):
        return False
    marks = _marks(spec, observed)
    return bool(known and marks) and marks <= set(known)


def observe_check(state, spec, *, evidence_root):
    if not state.approved:
        raise ValueError('check requires approved execution authority')
    if state.activity not in {'tests', 'checks'}:
        raise ValueError('not a check activity')
    now = time.time()
    remaining = deadline(state) - now
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
    env, environment = spec.environment()
    observed = _run(spec, state.plan.worktree, env, spec.timeout_seconds)
    after = fingerprint(state.plan.worktree)
    if after != before:
        raise ValueError('artifact changed during read-only check')
    if spec.environment()[1] != environment:
        raise ValueError('environment changed during check execution')
    outcome, reason = _outcome(spec, observed)
    if _pre_existing(evidence_root, state, spec, observed, outcome, reason):
        outcome, reason = Outcome.PASS, 'pre_existing'
    code = observed[0] if observed else None
    _remember(state, spec, observed, outcome, _known(evidence_root, state, spec))
    observed = None  # Never persist report text or test output.
    receipt = Receipt(spec.obligation, before.digest, state.plan.envelope.scope,
                      definition.inputs, definition.environment, definition.command, outcome,
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
