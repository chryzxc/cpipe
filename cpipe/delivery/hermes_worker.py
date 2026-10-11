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
import tempfile
import time

from .workers import WorkerResult
from .service import actor as active
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
            '--max-turns', '50', '--source', 'tool']
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
    usage = None
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
                            tokens = event.get('tokens')
                            if type(tokens) is dict and all(type(v) is int for v in tokens.values()):
                                usage = {k: tokens.get(k, 0) for k in
                                         ('input', 'output', 'total', 'cache_read', 'cache_write')}
                    if len(buffer) > MAX_LINE:
                        raise ValueError('worker record bound exceeded')
            try:
                rc = proc.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise ValueError('worker timeout after stream closed') from None
        if rc != 0 or final is None:
            raise ValueError('worker exited without a successful observed result')
        heartbeat()
        return WorkerResult(session, final, usage)
    finally:
        # Includes children that keep stdout open after the parent exits. No
        # unattended server processes are part of this worker contract.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # macOS: EPERM once only zombies remain
            pass
        proc.wait()
        for stream in (proc.stdin, proc.stdout):
            if stream and not stream.closed:
                stream.close()


def _delta(state):
    """What changed since the worker last looked: failing checks and open findings."""
    from .contracts import Outcome, UNAVAILABLE_REASONS
    from .review import NOTES
    lines = []
    latest = {r.obligation: r for r in state.receipts if r.artifact == state.artifact and r.obligation != 'review'}
    failed = sorted(o for o, r in latest.items() if r.outcome is Outcome.FAIL)
    if failed:
        lines.append('Failing checks on the current tree: ' + ', '.join(failed)
                     + '. Run them yourself with the commands in the approach to see the failure.')
    broken = sorted((o, r.reason) for o, r in latest.items() if r.outcome is Outcome.UNAVAILABLE)
    if broken:
        lines.append('Checks that produced no pass/fail result (run them yourself to see why, then fix the code '
                     'or tests inside write_paths; never change the approved commands or criteria):')
        lines += [f'- {o}: ' + UNAVAILABLE_REASONS.get(reason, 'no result') for o, reason in broken]
    if state.activity == 'write_tests':
        lines.append('The behavior test names must match the approved criteria exactly (describe prefixes are fine).')
    open_ = [r.finding for r in state.findings if r.finding.disposition == 'open' and r.finding.in_scope]
    if open_:
        lines.append('Open review findings to resolve:')
        lines += [f'- {f.id} [{f.severity}] ' + NOTES.get(f.root_cause, '(text unavailable; re-inspect all review axes)')
                  for f in open_]
    return '\n'.join(lines) + '\n' if lines else ''


def _prompt(state, resume, since=''):
    """Stable contract on a fresh session; on resume only the delta, since the session holds the rest."""
    if state.activity == 'write_tests':
        task = ('Write or fix only the behavior check\'s test (its exact command and criteria are in the '
                'approach) so it fails on the current code for the reason the change addresses. It must fail on an '
                'assertion, not a crash: if the code under test does not exist yet, add only a minimal stub '
                '(right signature, wrong result). Do not implement the change yet. Return only {"status":"done"}; the controller observes RED.')
    elif state.activity == 'implement':
        task = ('Make the minimum authorized correction for the failing checks and open findings. Fix every '
                'listed finding at its root cause in this one pass, then run the approved check commands and '
                'inspect the callers of anything you changed so the fix introduces no regression. '
                'Return only {"status":"done"}. Passing checks and completion are the controller’s '
                'job, not a prose assertion.')
    if state.activity in {'write_tests', 'implement'} and resume:
        return 'Same delivery, next round. Activity: ' + state.activity + '.\n' + _delta(state) + task
    prompt = ('You are a directly executing worker in an explicitly selected cpipe workflow. '
        'Do the assigned work with your own tools; do not delegate, invoke external coding CLIs, '
        'create tasks, or call cpipe/kanban lifecycle tools. The controller owns authority. '
        'Do not commit, push, open PRs, or modify files outside write_paths. '
        'Follow project instructions such as AGENTS.md; use installed skills only when clearly relevant. '
        'Credentials must never be written; replace any encountered values with [REDACTED]. '
        'If your own instructions forbid this work, return only {"status":"blocked","reason":"<one line>"} '
        'without changing files; another approved worker takes over.\n'
        'Frozen contract:\n' + encoded(state.plan.to_dict()) + '\n'
        'Current activity: ' + state.activity + '\n' + _delta(state))
    if state.activity != 'review':
        return prompt + task
    from .review import REVIEW_AXES
    schema = dict(status='approved|changes_requested', artifact=state.artifact,
        reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES),
        findings=[], verified_ids=[])
    return prompt + ('Read-only independent review: inspect the complete frozen base-to-candidate '
        'range, goal, acceptance, preserved behavior and evidence. Do not edit anything. '
        'Every coverage axis requires actual inspection, not a checkbox assertion. Look for: regressions '
        '(run the project\'s own tests; check callers and behavior outside the diff), new bugs and unhandled '
        'edge cases, security issues, and code to simplify (duplication, dead code, needless abstraction, '
        'comments restating code, reimplemented existing helpers). Report only issues worth changing, not '
        'style preferences; every open in-scope finding sends the change back to the builder and is reviewed again. '
        + (f'This is a re-review. Only the builder\'s fixes changed since the last review; run '
           f'`git diff {since}` to see them. Confirm each prior finding is resolved, then review that diff and its '
           'effect on callers for regressions and new bugs. Do not raise new issues in code unchanged since the '
           'last review unless high or critical; it was already reviewed. ' if since else
           'Review all listed axes and report all findings in one verdict, so the builder can fix them in one pass. ')
        + 'For each prior finding ID '
        'listed above, confirm it is resolved before putting it in verified_ids; otherwise leave it unresolved. '
        'Return exactly the following JSON schema, replacing status with approved or '
        'changes_requested; each new finding has exactly id (empty string), criterion '
        '(review), root_cause (the problem and the concrete fix, no credentials), severity '
        '(low/medium/high/critical), in_scope (boolean), disposition (open). '
        'Only existing prior IDs may appear in verified_ids. Do not add fields.\n'
        + encoded(schema))


def _git(cwd, *args, env=None, check=True):
    env = env or {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    return subprocess.run(['git', *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                          check=check, timeout=120).stdout.decode().strip()


def snapshot(cwd):
    """Tree id of the whole worktree, new files included, without touching the real index."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env['GIT_INDEX_FILE'] = os.path.join(tmp, 'index')
        _git(cwd, 'read-tree', 'HEAD', env=env)
        _git(cwd, 'add', '-A', env=env)
        return _git(cwd, 'write-tree', env=env)


def _ref(state):
    return 'refs/cpipe/reviewed/' + state.plan.envelope.delivery_id


class HermesWorker:
    def __call__(self, state, *, on_session, heartbeat, timeout_seconds):
        actor = active(state, 'reviewer' if state.activity == 'review' else 'builder')
        if actor.engine != 'hermes':
            raise ValueError('unsupported execution engine')
        resume = state.builder_session if state.activity in {'write_tests', 'implement'} else ''
        since = now = ''
        if state.activity == 'review':
            try:  # re-reviews look only at what changed since the last review
                now = snapshot(state.plan.worktree)
                last = _git(state.plan.worktree, 'rev-parse', '-q', '--verify', _ref(state) + '^{tree}', check=False)
                since = f'{last} {now}' if state.review_cycles and last and last != now else ''
            except (OSError, subprocess.SubprocessError):
                now = ''
        prompt = _prompt(state, resume, since)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('HERMES_KANBAN_', 'GIT_')) and k != 'PYTHONPATH'}
        result = execute_stream(command(actor, state.plan.worktree, resume, timeout_seconds),
            prompt=prompt, cwd=state.plan.worktree, env=env, model=actor.model,
            expected_session=resume, on_session=on_session, heartbeat=heartbeat,
            timeout_seconds=timeout_seconds)
        if now:  # the next re-review diffs from this tree
            _git(state.plan.worktree, 'update-ref', _ref(state), now, check=False)
        return result
