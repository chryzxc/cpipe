"""Bounded Hermes CLI stream adapter; trusted local, unsandboxed execution.

Only host-generated init/result records establish a child session. Tool output,
text deltas and diagnostics are discarded, never written to cpipe logs. This
adapter does not claim to constrain the child's filesystem or network access.
"""
from __future__ import annotations

import json
import math
import os
import selectors
import signal
import subprocess
import time

from .workers import WorkerResult
from .journal import encoded

MAX_LINE = 256 * 1024
MAX_STREAM = 32 * 1024 * 1024
MAX_RESULT = 64 * 1024


def command(actor, cwd, resume, timeout_seconds):
    from ..runtime.launcher import module_command
    args = ['-p', actor.profile, 'chat', '--query-file', '-', '--oneshot',
            '--format', 'stream-json', '--in', str(cwd), '--no-restore-cwd',
            '--model', actor.model, '--provider', actor.provider,
            '--run-budget', str(max(1, math.ceil(timeout_seconds))),
            '--max-turns', '50', '--ignore-rules', '--source', 'tool']
    if resume:
        args += ['--resume', resume]
    return module_command('hermes_cli.main', *args)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate structured worker field')
        result[key] = value
    return result


def structured(text):
    if type(text) is not str or len(text.encode()) > MAX_RESULT:
        raise ValueError('invalid or oversized structured worker result')
    text = text.strip()
    if text.startswith('```json\n') and text.endswith('\n```'):
        text = text[8:-4]
    try:
        value = json.loads(text, object_pairs_hook=_unique)
    except (ValueError, RecursionError) as exc:
        raise ValueError('missing valid structured worker result') from None
    if type(value) is not dict:
        raise ValueError('structured worker result must be an object')
    return value


def execute_stream(argv, *, prompt, cwd, env, model, expected_session,
                   on_session, heartbeat, timeout_seconds):
    """Read the real JSONL protocol with lease checks and a hard wall clock."""
    data = prompt.encode()
    if len(data) > MAX_RESULT or timeout_seconds <= 0:
        raise ValueError('invalid worker prompt or budget')
    started = time.monotonic()
    deadline = started + timeout_seconds
    proc = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    session = ''
    final = None
    buffer = b''
    count = 0
    offset = 0
    next_heartbeat = started
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(proc.stdout.fileno(), False)
            os.set_blocking(proc.stdin.fileno(), False)
            selector.register(proc.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(proc.stdin, selectors.EVENT_WRITE, 'stdin')
            while selector.get_map():
                now = time.monotonic()
                if now >= deadline:
                    raise ValueError('worker timeout; no result accepted')
                if now >= next_heartbeat:
                    heartbeat()
                    next_heartbeat = now + min(5, timeout_seconds / 4)
                for key, _ in selector.select(min(.1, deadline - now)):
                    if key.data == 'stdin':
                        try:
                            offset += os.write(proc.stdin.fileno(), data[offset:offset + 4096])
                        except BrokenPipeError:
                            offset = len(data)
                        if offset == len(data):
                            selector.unregister(proc.stdin)
                            proc.stdin.close()
                        continue
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        if buffer.strip():
                            raise ValueError('incomplete worker stream record')
                        selector.unregister(proc.stdout)
                        break
                    count += len(chunk)
                    buffer += chunk
                    if count > MAX_STREAM:
                        raise ValueError('worker output bound exceeded')
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        if len(line) > MAX_LINE:
                            raise ValueError('worker record bound exceeded')
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeError, RecursionError):
                            continue  # bootstrap diagnostics, never retained
                        if type(event) is not dict:
                            continue
                        if event.get('type') == 'system' and event.get('subtype') == 'init':
                            sid = event.get('session_id')
                            if (session or type(sid) is not str or not sid
                                    or any(ord(c) < 32 for c in sid)
                                    or event.get('model') != model
                                    or (expected_session and sid != expected_session)):
                                raise ValueError('worker init model/session mismatch')
                            on_session(sid)
                            session = sid
                        elif event.get('type') == 'result':
                            if (not session or final is not None
                                    or event.get('session_id') != session
                                    or type(event.get('exit_code')) is not int
                                    or event['exit_code'] != 0 or event.get('error')):
                                raise ValueError('worker terminal result session/status mismatch')
                            final = structured(event.get('text'))
                    if len(buffer) > MAX_LINE:
                        raise ValueError('worker record bound exceeded')
            try:
                rc = proc.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise ValueError('worker timeout after stream closed') from None
        if rc != 0 or final is None:
            raise ValueError('worker exited without a successful observed result')
        heartbeat()
        return WorkerResult(session, final)
    finally:
        # Includes children that keep stdout open after the parent exits. No
        # unattended server processes are part of this worker contract.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        for stream in (proc.stdin, proc.stdout):
            if stream and not stream.closed:
                stream.close()


class HermesWorker:
    def __call__(self, state, *, on_session, heartbeat, timeout_seconds):
        actor = next(a for a in state.plan.actors if a.role == (
            'reviewer' if state.activity == 'review' else 'builder'))
        if actor.engine != 'hermes':
            raise ValueError('unsupported v2 execution engine')
        resume = state.builder_session if state.activity == 'implement' else ''
        prompt = ('You are a directly executing worker in an explicitly selected cpipe v2 workflow. '
            'Do the assigned work with your own tools; do not delegate, invoke external coding CLIs, '
            'create tasks, or call cpipe/kanban lifecycle tools. The controller owns authority. '
            'Do not commit, push, open PRs, or modify files outside write_paths. '
            'Credentials must never be written; replace any encountered values with [REDACTED].\n'
            'Frozen contract:\n' + encoded(state.plan.to_dict()) + '\n'
            'Current activity: ' + state.activity + '\n'
            'Prior findings: ' + encoded([r.to_dict() for r in state.findings]) + '\n')
        if state.activity == 'review':
            from .review import REVIEW_AXES
            schema = dict(status='approved|changes_requested', artifact=state.artifact,
                reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES),
                findings=[], verified_ids=[])
            prompt += ('Read-only independent review: inspect the complete frozen base-to-candidate '
                'range, goal, acceptance, preserved behavior and evidence. Do not edit anything. '
                'Every coverage axis requires actual inspection, not a checkbox assertion. '
                'Review all listed axes and report all findings in one verdict. Existing root-cause '
                'identities are hashes for privacy; never claim a prior ID verified unless the '
                'current complete inspection supports its resolution. Otherwise leave it unresolved. '
                'Return exactly the following JSON schema, replacing status with approved or '
                'changes_requested; each new finding has exactly id (empty string), criterion '
                '(review), root_cause (nonempty description, no credentials), severity '
                '(low/medium/high/critical), in_scope (boolean), disposition (open). '
                'Only existing prior IDs may appear in verified_ids. Do not add fields.\n'
                + encoded(schema))
        else:
            prompt += ('For implement: inspect the failing frozen check and acceptance; when '
                'prior findings are hashed, independently re-inspect all review axes to identify '
                'the in-scope correction. Make the minimum authorized correction, then return '
                'only {"status":"done"}. Passing checks and completion are the controller’s '
                'job, not a prose assertion.')
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('HERMES_KANBAN_', 'GIT_')) and k != 'PYTHONPATH'}
        return execute_stream(command(actor, state.plan.worktree, resume, timeout_seconds),
            prompt=prompt, cwd=state.plan.worktree, env=env, model=actor.model,
            expected_session=resume, on_session=on_session, heartbeat=heartbeat,
            timeout_seconds=timeout_seconds)
