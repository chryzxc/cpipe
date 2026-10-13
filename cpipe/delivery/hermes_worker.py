"""Bounded Hermes CLI stream adapter; trusted local, unsandboxed execution.

Only host-generated init/result records establish a child session. Tool output,
text deltas and diagnostics are discarded, never written to cpipe logs. This
adapter does not claim to constrain the child's filesystem or network access.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
import selectors
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .workers import WorkerResult
from .service import actor as active
from .journal import encoded

MAX_LINE = 256 * 1024
MAX_STREAM = 32 * 1024 * 1024
MAX_RESULT = 64 * 1024
# Model turns per step: a worker that cannot finish in these is lost, and a fresh attempt is cheaper.
TURNS = {'write_tests': 30, 'implement': 40, 'review': 30}
# How builders work: every background job, poll loop or reinstall is paid for in tokens on every later turn.
RULES = ('Work in the foreground: no background jobs, polling loops, sleeps or watch modes. cpipe runs the '
         'approved checks after you return and sends you any failures, so do not run the full suite; at most run '
         'the one narrow test you are working on. Inline scripts (-c/-e flags) may be blocked; write a small file '
         'instead. Do not install dependencies, download tools or debug the environment: if something missing '
         'stops you, change nothing more and return only {"status":"blocked","reason":"environment: <what is '
         'missing>"}; the user fixes it and the delivery continues. ')


def command(actor, cwd, resume, timeout_seconds, turns=50):
    from ..runtime.launcher import module_command
    args = ['-p', actor.profile, 'chat', '--query-file', '-', '--oneshot',
            '--format', 'stream-json', '--in', str(cwd), '--no-restore-cwd',
            '--model', actor.model, '--provider', actor.provider,
            '--run-budget', str(max(1, math.ceil(timeout_seconds))),
            '--max-turns', str(turns), '--source', 'tool']
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


class Unfinished(ValueError):
    """The worker's turn ended without the JSON answer (out of turns, or prose): its session can still give it."""
    def __init__(self, origin, session, tokens):
        super().__init__('worker ended without a structured result')
        self.origin, self.session, self.tokens = origin, session, tokens  # started as origin, resumable as session


# Sent to a session that ran out of turns: no more work, only the answer it owes.
SALVAGE = ('You ran out of turns. Do not call any tools. Reply now with only the JSON this step asks for, '
           'describing the work as it stands; if it is not finished, use {"status":"blocked","reason":"<what is '
           'left and why>"}.')


def execute_stream(argv, *, prompt, cwd, env, model, expected_session,
                   on_session, heartbeat, timeout_seconds, pidfile=None):
    """Read the real JSONL protocol with lease checks and a hard wall clock."""
    data = prompt.encode()
    if len(data) > MAX_RESULT or timeout_seconds <= 0:
        raise ValueError('invalid worker prompt or budget')
    started = time.monotonic()
    deadline = started + timeout_seconds
    proc = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if pidfile:  # if this process dies, the next run stops the worker instead of letting it spend on
        Path(pidfile).write_text(str(proc.pid))
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
                            # Context compression may rotate the session id mid-run; the answer is still this run's.
                            sid = event.get('session_id')
                            if (not session or final is not None or type(sid) is not str or not sid
                                    or any(ord(c) < 32 for c in sid) or type(event.get('exit_code')) is not int):
                                raise ValueError('worker terminal result session/status mismatch')
                            tokens = event.get('tokens')
                            if type(tokens) is dict and all(type(v) is int for v in tokens.values()):
                                usage = {k: tokens.get(k, 0) for k in
                                         ('input', 'output', 'total', 'cache_read', 'cache_write')}
                            try:  # a run that hit its turn limit exits 1, but its last answer may be the JSON
                                final = structured(event.get('text'))
                            except ValueError:
                                raise Unfinished(session, sid, usage) from None
                    if len(buffer) > MAX_LINE:
                        raise ValueError('worker record bound exceeded')
            try:
                proc.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise ValueError('worker timeout after stream closed') from None
        if final is None:
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
        if pidfile:
            Path(pidfile).unlink(missing_ok=True)
        for stream in (proc.stdin, proc.stdout):
            if stream and not stream.closed:
                stream.close()


def _delta(state):
    """What changed since the worker last looked: failing checks and open findings."""
    from .check_runner import TAILS
    from .contracts import Outcome, UNAVAILABLE_REASONS
    from .review import NOTES
    from .workers import DISPUTES
    lines = []
    latest = {r.obligation: r for r in state.receipts if r.artifact == state.artifact and r.obligation != 'review'}
    failed = sorted(o for o, r in latest.items() if r.outcome is Outcome.FAIL)
    if failed:
        lines.append('Failing checks on the current tree: ' + ', '.join(failed)
                     + '. Their output is below; re-run a check only to confirm your fix.')
    broken = sorted((o, r.reason) for o, r in latest.items() if r.outcome is Outcome.UNAVAILABLE)
    if broken:
        lines.append('Checks that produced no pass/fail result (fix the code or tests from the output below; '
                     'never change the approved commands or criteria):')
        lines += [f'- {o}: ' + UNAVAILABLE_REASONS.get(reason, 'no result') for o, reason in broken]
    for o in failed + [o for o, _ in broken]:
        artifact, missing, tail = TAILS.get(state.plan.envelope.delivery_id + ':' + o, (None, (), ''))
        if artifact != state.artifact:
            continue
        if missing:
            lines.append(f'{o}: these approved criteria never appeared in the output (the test name must contain '
                         'each exactly): ' + ', '.join(missing))
        if tail.strip():
            lines.append(f'{o} output (redacted):\n```\n{tail.strip()}\n```')
    from .findings import blocks
    open_ = [r.finding for r in state.findings if blocks(r.finding)]
    disputed = {f.id: DISPUTES.get(state.plan.envelope.delivery_id + ':' + f.id) for f in open_}
    if open_:
        lines.append('Open review findings to resolve:' if state.activity != 'review' else
                     'Prior blocking findings (put each in verified_ids only once resolved):')
        lines += [f'- {f.id} [{f.severity}] ' + NOTES.get(f.root_cause, '(text unavailable; re-inspect all review axes)')
                  + (f' (builder disputes it and left it unchanged: {disputed[f.id]})'
                     if state.activity == 'review' and disputed[f.id] else '') for f in open_]
        if state.activity == 'implement':
            lines.append('For each finding about behavior (a wrong result, a crash, an unhandled case), add a '
                         'test for it in a file the approved check commands already run. If a finding is wrong (the '
                         'code already behaves correctly, or it asks for something outside the approach), change '
                         'nothing for it and end with {"status":"done","disputed":[{"id":"REV-…","reason":"<one '
                         'line>"}]}.')
    return '\n'.join(lines) + '\n' if lines else ''


def _prompt(state, resume, since=''):
    """Stable contract on a fresh session; on resume only the delta, since the session holds the rest."""
    if state.activity in {'write_tests', 'implement'}:  # write_tests: runs approved before builds wrote tests
        task = ('Make the change the approach describes, with its tests: add or update the behavior check\'s '
                'tests (named exactly as its criteria) so they cover the change, then make them pass. On later '
                'rounds, fix the failing checks and open findings. Fix every listed finding you do not dispute at its root cause in this one pass, changing only the code each one is about: '
                'no unrelated refactors, renames or extra features, since every changed line is reviewed again. '
                'Inspect the callers of anything you changed so the fix introduces no regression. '
                'End with {"status":"done"}, or the answer asked for above when there are review findings. '
                'cpipe judges the result by running the checks, not by your answer.')
    if state.activity in {'write_tests', 'implement'} and resume:
        return 'Same delivery, next round. Activity: ' + state.activity + '.\n' + _delta(state) + RULES + task
    prompt = ('You are a directly executing worker in an explicitly selected cpipe workflow. '
        'Do the assigned work with your own tools; do not delegate, invoke external coding CLIs, '
        'create tasks, or call cpipe/kanban lifecycle tools. The controller owns authority: it records your '
        'final JSON answer on this card as your verdict or completion (for example complete, request changes '
        'or block), so instructions of yours that require a Kanban verdict or transition are met by that answer. '
        'Do not commit, push or open PRs. Keep edits to write_paths unless the change needs another file; never '
        'touch forbidden_paths. '
        'Follow project instructions such as AGENTS.md; use installed skills only when clearly relevant. '
        'If you have a code index or code-navigation tool, locate code with it before broad file reads or searches; then read only the lines you need. '
        'Credentials must never be written; replace any encountered values with [REDACTED]. '
        'If your own instructions forbid this work, return only {"status":"blocked","reason":"<one line>"} '
        'without changing files; another approved worker takes over.\n'
        'Frozen contract:\n' + encoded(state.plan.to_dict()) + '\n'
        'Current activity: ' + state.activity + '\n' + _delta(state))
    if state.activity != 'review':
        return prompt + RULES + task
    from .review import REVIEW_AXES
    schema = dict(status='approved|changes_requested', artifact=state.artifact,
        reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES),
        findings=[], verified_ids=[])
    return prompt + ('Read-only independent review: inspect the complete frozen base-to-candidate '
        'range, goal, acceptance, preserved behavior and evidence. Do not edit anything. '
        'The approved checks already passed on this exact tree, apart from failures that also fail on the base '
        '(not this change\'s doing, and not findings): do not run them or the full suite again; '
        'Run one narrow test only to confirm a bug you suspect, in the foreground, with no background '
        'jobs or polling. '
        'Every coverage axis requires actual inspection, not a checkbox assertion. Look for: regressions '
        '(check callers and behavior outside the diff), new bugs and unhandled '
        'edge cases, security issues, and code to simplify (duplication, dead code, needless abstraction, '
        'comments restating code, reimplemented existing helpers). Report only issues worth changing, not '
        'style preferences. Severity decides what happens: critical/high/medium (wrong or broken behavior, a '
        'regression, a security or data-loss risk, an unmet acceptance criterion, a real maintenance cost) sends '
        'the change back to the builder, and every fix is reviewed again; low is advice listed in the report and '
        'never blocks. Each finding names the file and line and how it fails or what it costs; without that it '
        'is low at most. '
        + (f'This is re-review round {state.review_cycles + 1}. Only the builder\'s fixes changed since the last '
           f'review; run `git diff {since}` to see them. Confirm each prior finding is resolved, then review that '
           'diff and its effect on callers for regressions and new bugs. A new finding is medium or above only if '
           'those fixes caused it; do not raise new issues in code unchanged since the last review unless high '
           'or critical, it was already reviewed. Do not re-raise a resolved finding in other words, and do not '
           'ask to undo what an earlier finding asked for. If the builder disputes a finding, put it in verified_ids '
           'when its reason holds; keep it open only if you are certain it is still wrong, since an unsettled '
           'dispute goes to the user. ' if since else
           'Review all listed axes and report all findings in one verdict, so the builder can fix them in one pass. ')
        + 'For each prior finding ID '
        'listed above, confirm it is resolved before putting it in verified_ids; otherwise leave it unresolved. '
        'Return exactly the following JSON schema, with status changes_requested if any finding is medium or '
        'above (or a prior one is unresolved), else approved; each new finding has exactly id (empty string), criterion '
        '(the one coverage axis it breaks, e.g. security or correctness), root_cause (the problem and the concrete fix, no credentials), severity '
        '(low/medium/high/critical), in_scope (boolean), disposition (open). '
        'Only existing prior IDs may appear in verified_ids. Do not add fields.\n'
        + encoded(schema))


def behavior_check(checks, outcome='failing on an assertion'):
    """The exact command and names cpipe judges by, so the builder gets them right in one round."""
    check = next((c for c in checks if c.obligation == 'behavior'), None)
    if check is None:
        return ''
    argv = shlex.join(check.argv).replace('{report}', '<any temporary file>')
    path = dict(check.env).get('PATH')
    if path:  # the same programs cpipe runs, whatever this shell's own PATH finds first
        argv = 'env PATH=' + shlex.quote(path) + ' ' + argv
    return ('\nBefore returning, run the behavior check exactly as cpipe will, once, in the foreground: `' + argv
            + f'`. Its output must show each of these test names {outcome}: '
            + ', '.join(check.criteria) + '. If a name is missing, differs, or matches more than one test, rename '
            'the test and run it again; this is the one test you may run. A mismatch costs a whole new round.\n')


def ask(actor, cwd, prompt, *, heartbeat, timeout_seconds):
    """One fresh worker turn outside the step loop (writing or verifying a pull request); returns its JSON."""
    if actor.engine != 'hermes':
        raise ValueError('unsupported execution engine')
    stop_orphan(cwd)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('HERMES_KANBAN_', 'GIT_')) and k != 'PYTHONPATH'}
    return execute_stream(command(actor, cwd, '', timeout_seconds, 20), prompt=prompt, cwd=cwd, env=env,
                          model=actor.model, expected_session='', on_session=lambda _: None, heartbeat=heartbeat,
                          timeout_seconds=timeout_seconds, pidfile=worker_pidfile(cwd)).payload


def worker_pidfile(worktree):
    """Where the running worker's process id is kept: the worktree's own git dir, never in the diff."""
    return Path(_git(worktree, 'rev-parse', '--path-format=absolute', '--git-path', 'cpipe-worker.pid'))


def stop_orphan(worktree):
    """Stop a worker left running by a cpipe process that died (a Hermes restart): it would keep spending
    tokens unobserved and hold its session, so every retry fails to resume. Only a process still working in
    this worktree is stopped; a reused process id is left alone."""
    try:
        path = worker_pidfile(worktree)
        pid = int(path.read_text())
    except (OSError, ValueError, subprocess.SubprocessError):
        return
    try:
        argv = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)], capture_output=True, text=True,
                              timeout=10).stdout
        if str(worktree) in argv and os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
            for _ in range(50):  # until it is gone and its session is free again
                os.kill(pid, 0)
                time.sleep(.1)
    except (OSError, subprocess.SubprocessError):
        pass  # gone already
    path.unlink(missing_ok=True)


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


def _good(plan):
    return 'refs/cpipe/good/' + plan.envelope.delivery_id


def _index(cwd):
    return Path(_git(cwd, 'rev-parse', '--path-format=absolute', '--git-path', 'index'))


def keep(plan, store):
    """Remember the worktree (files and index) as the last verified state."""
    _git(plan.worktree, 'update-ref', _good(plan), snapshot(plan.worktree))
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    index = _index(plan.worktree)
    if index.exists():
        shutil.copyfile(index, store / 'index')
    else:
        (store / 'index').unlink(missing_ok=True)


def reattach(plan):
    """Back on the approved branch at the approved base; any commits become uncommitted edits."""
    _git(plan.worktree, 'symbolic-ref', 'HEAD', 'refs/heads/' + plan.envelope.branch)
    _git(plan.worktree, 'reset', '-q', '--soft', plan.base_commit)  # workers never commit; undo any that did


def changed_since_base(plan):
    """Paths whose content differs from the approved base, new files included."""
    return _git(plan.worktree, 'diff-tree', '-r', '-z', '--no-renames', '--name-only',
                plan.base_commit + '^{tree}', snapshot(plan.worktree)).split('\0')[:-1]


def roll_back(plan, store):
    """Undo a failed step's edits, staging, commits or branch switch; False if nothing was kept."""
    cwd = plan.worktree
    tree = _git(cwd, 'rev-parse', '-q', '--verify', _good(plan) + '^{tree}', check=False)
    if not tree:
        return False
    reattach(plan)
    now = snapshot(cwd)
    if now != tree:
        parts = _git(cwd, 'diff-tree', '-r', '-z', '--no-renames', '--name-status', tree, now).split('\0')
        changed = list(zip(parts[::2], parts[1::2]))
        for status, name in changed:
            if status == 'A':
                (Path(cwd) / name).unlink(missing_ok=True)
        restore = [name for status, name in changed if status != 'A']
        if restore:
            with tempfile.TemporaryDirectory() as tmp:
                env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
                env['GIT_INDEX_FILE'] = os.path.join(tmp, 'index')
                _git(cwd, 'read-tree', tree, env=env)
                _git(cwd, 'checkout-index', '-f', '--', *restore, env=env)
    index = _index(cwd)
    if (store / 'index').exists():
        shutil.copyfile(store / 'index', index)
    else:
        index.unlink(missing_ok=True)
    return True


def _put(cwd, tree, names):
    """Make these worktree files match tree (absent there: removed). The index is untouched."""
    listed = _git(cwd, 'ls-tree', '-r', '-z', '--name-only', tree, '--', *names).split('\0')[:-1]
    for name in set(names) - set(listed):
        (Path(cwd) / name).unlink(missing_ok=True)
    if listed:
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            env['GIT_INDEX_FILE'] = os.path.join(tmp, 'index')
            _git(cwd, 'read-tree', tree, env=env)
            _git(cwd, 'checkout-index', '-f', '--', *listed, env=env)


def _hold(plan):
    return 'refs/cpipe/hold/' + plan.envelope.delivery_id


def _diff(cwd, a, b):
    return set(_git(cwd, 'diff-tree', '-r', '-z', '--no-renames', '--name-only', a, b).split('\0')[:-1])


def release(plan):
    """Put back files moved aside by at_base, also after a crash in the middle of it."""
    cwd = plan.worktree
    held = _git(cwd, 'rev-parse', '-q', '--verify', _hold(plan) + '^{tree}', check=False)
    if not held:
        return
    origin = (_git(cwd, 'rev-parse', '-q', '--verify', _hold(plan) + '-from', check=False)
              or plan.base_commit + '^{tree}')
    # Only the files at_base moved: never another edit made meanwhile.
    names = _diff(cwd, held, snapshot(cwd)) & _diff(cwd, origin, held)
    if names:
        _put(cwd, held, sorted(names))
    _git(cwd, 'update-ref', '-d', _hold(plan) + '-from', check=False)
    _git(cwd, 'update-ref', '-d', _hold(plan))


@contextmanager
def at_base(plan, names, tree=None):
    """Run with these files as they are at the approved base (or at tree), then put them back."""
    release(plan)
    origin = tree or plan.base_commit + '^{tree}'
    if names:  # held: origin plus these files as they are, so release knows exactly what moved
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            env['GIT_INDEX_FILE'] = os.path.join(tmp, 'index')
            _git(plan.worktree, 'read-tree', origin, env=env)
            _git(plan.worktree, 'add', '-A', '--', *names, env=env)
            if tree:
                _git(plan.worktree, 'update-ref', _hold(plan) + '-from', tree)
            _git(plan.worktree, 'update-ref', _hold(plan), _git(plan.worktree, 'write-tree', env=env))
    try:
        if names:
            _put(plan.worktree, origin, names)
        yield
    finally:
        release(plan)


def _ref(state):
    return 'refs/cpipe/reviewed/' + state.plan.envelope.delivery_id


class HermesWorker:
    def __call__(self, state, *, on_session, heartbeat, timeout_seconds):
        actor = active(state, 'reviewer' if state.activity == 'review' else 'builder')
        if actor.engine != 'hermes':
            raise ValueError('unsupported execution engine')
        resume = state.builder_session if state.activity in {'write_tests', 'implement'} else ''
        stop_orphan(state.plan.worktree)  # it would hold the session and keep spending
        try:
            pidfile = worker_pidfile(state.plan.worktree)
        except (OSError, subprocess.SubprocessError):
            pidfile = None
        since = now = ''
        if state.activity == 'review':
            try:  # re-reviews look only at what changed since the last review
                now = snapshot(state.plan.worktree)
                last = _git(state.plan.worktree, 'rev-parse', '-q', '--verify', _ref(state) + '^{tree}', check=False)
                since = f'{last} {now}' if state.review_cycles and last else ''
            except (OSError, subprocess.SubprocessError):
                now = ''
        prompt = _prompt(state, resume, since)
        if state.activity in {'write_tests', 'implement'} and not resume:
            prompt += behavior_check(getattr(self, 'checks', ()), 'passing')
        note, self.note = getattr(self, 'note', ''), ''
        if note:  # the controller's reason the previous attempt failed (a fixed label, never worker text)
            prompt += '\n' + note
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('HERMES_KANBAN_', 'GIT_')) and k != 'PYTHONPATH'}
        started = time.monotonic()
        try:
            result = execute_stream(command(actor, state.plan.worktree, resume, timeout_seconds,
                                            TURNS.get(state.activity, 50)),
                prompt=prompt, cwd=state.plan.worktree, env=env, model=actor.model,
                expected_session=resume, on_session=on_session, heartbeat=heartbeat,
                timeout_seconds=timeout_seconds, pidfile=pidfile)
        except Unfinished as exc:
            if state.activity != 'review':  # the checks judge a build, not its answer
                return WorkerResult(exc.origin, {'status': 'done'}, exc.tokens)
            # Self-repair: ask the same session for its answer (a few turns, no tools) instead of
            # throwing the work away and paying for a fresh attempt.
            left = min(300, timeout_seconds - (time.monotonic() - started))
            if left < 30:
                raise ValueError('worker ran out of turns and time; no result accepted') from None
            result = execute_stream(command(actor, state.plan.worktree, exc.session, left, 3),
                prompt=SALVAGE, cwd=state.plan.worktree, env=env, model=actor.model,
                expected_session=exc.session, on_session=lambda _: None, heartbeat=heartbeat,
                timeout_seconds=left, pidfile=pidfile)
            tokens = ({k: exc.tokens[k] + result.tokens[k] for k in exc.tokens}
                      if exc.tokens and result.tokens else exc.tokens or result.tokens)
            result = WorkerResult(exc.origin, result.payload, tokens)  # the session this step was bound to
        if now:  # the next re-review diffs from this tree
            _git(state.plan.worktree, 'update-ref', _ref(state), now, check=False)
        return result
