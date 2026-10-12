"""Session-bound task commands with explicit frozen-plan consent."""
from contextlib import contextmanager
from collections import Counter
import contextvars
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading
import time

from .delivery.journal import Journal, encoded
from .delivery.service import Service, State
from .delivery import controller

SCHEMA = dict(name='cpipe_execute_intent', description='Optional cpipe workflow. Consume an explicit /cp-run command receipt. '
    'Ordinary requests use normal tools. Plans require explicit worktree, scope, test-command checks and distinct saved '
    'builder/reviewer profiles. Prepare a reviewable Markdown draft, then freeze and approve before build. Never merges; opens a draft PR only when the user asked for one.',
    parameters=dict(type='object', additionalProperties=False,
        properties=dict(token=dict(type='string'), operation=dict(type='string', enum=['prepare', 'plan', 'build', 'status', 'stop', 'spawn']),
            arguments=dict(type='object', description='prepare: {spec, approach, expected_revision} (0 initially; current revision for edits). '
                'approach: kind (bug|feature|change), depth (focused|detailed), risk (normal|high), summary; '
                'evidence, steps, decisions, risks are string lists. Bugs need evidence; feature/high-risk work needs detailed decisions. '
                'spec: worktree (the checkout you investigated; cpipe never changes it and runs the delivery in a new '
                'worktree of its own, so other worktrees inside the checkout and unrelated edits do not matter), repo, '
                'base (branch, tag or commit the delivery starts from; when it is what the checkout has checked out, '
                'its uncommitted changes are included), title (optional: a short name for the task on the board, at most 60 '
                'characters; start with the task\'s own ID only if it has one), goal, acceptance, '
                'non_goals, write_paths, forbidden_paths, builder and reviewer (each a profile, or an ordered list: the next takes over if one refuses or keeps failing), '
                'seconds, checks, and draft_pr (true only when the user asked for a pull request; omit otherwise) with branch '
                '(required with draft_pr: a new branch name following the repository\'s and the user\'s conventions). Each check has '
                'obligation, argv (the repository\'s own test command as a list, run in the worktree, any language; '
                'argv[0] on PATH or absolute), timeout_seconds and criteria (test names). If argv contains {report}, '
                'it is replaced by a private report path (e.g. pytest --junitxml={report}, vitest --reporter=junit '
                '--outputFile={report}, jest --json --outputFile={report}) and each criterion matches one reported classname::name or name; otherwise '
                'pass/fail is the exit code and every criterion must appear in the output. Prefer {report}. '
                'Checks run in the new worktree. It gets copies of the checkout\'s installed dependency folders (such as '
                'node_modules) when the lockfiles match, but never a virtualenv, and copies can be unavailable: use '
                'commands that provision their own dependencies (uv run, go test, cargo test) or install only when '
                'missing, e.g. ["sh","-c","[ -d node_modules ] || npm ci; npx vitest run ..."]; never name the '
                'investigated checkout in a command. '
                'Exactly one check has obligation behavior: the test proving the change; it must fail before implementing, '
                'and the builder writes it first, so its file must be inside write_paths. '
                'Also add a regression check running the existing tests for every module the change touches and '
                'their callers (and the type checker or linter for those files, if the repository has one), so a '
                'change cannot pass while breaking code next to it. '
                'For /cp-run, plan: {draft_revision} freezes the reviewed draft; build: {delivery_id} from that plan after the user approves. '
                'Run status: {}. For several deliveries in one /cp-run, add task (short label) to every call for each. '
                'Existing plan receipts accept their full spec. For /cp-spawn, spawn: {tasks: [{title (a specific chat title, at most 80 '
                'characters), cwd (the repository path), brief}]}. Other operations: only delivery_id.')),
        required=['token', 'operation', 'arguments']))


def session(explicit=None):
    if explicit:
        return explicit
    from gateway.session_context import get_session_env
    return get_session_env('HERMES_SESSION_ID') or ''


def session_key():
    from gateway.session_context import get_session_env
    return get_session_env('HERMES_SESSION_KEY') or session()


@contextmanager
def scope():
    from hermes_constants import get_hermes_home
    from hermes_cli.profiles import get_active_profile_name
    with Journal.for_profile(get_hermes_home()) as journal:
        yield Service(journal), get_active_profile_name(), get_hermes_home()


_CTX = None  # plugin context, to message the originating chat when a run settles
_RUNS = {}  # delivery_id -> background thread
CRASH_RETRIES = 2  # a run that crashes again without any progress is parked after this many retries
RETRY_PAUSE = 30  # seconds, grows with each retry without progress
PARK = 600  # seconds a parked run waits before the watchdog tries it again
WATCH = 60  # seconds between watchdog passes
SETTLE = 15  # seconds build waits, so a run that fails at once is reported in the reply itself
log = logging.getLogger('cpipe')

HEADLINES = {'locally_ready': 'done and ready for your review',
             'completed': 'done: its draft pull request is open for your review',
             'stopped': 'stopped: its time or turn budget ran out',
             'diagnosis': 'stopped: every approved builder got stuck (no valid failing test, or review findings none could resolve)',
             'waiting': 'stopped: it needs you (every approved builder or reviewer declined or kept failing, or the environment is missing something)',
             'cancelled': 'cancelled'}


def _runner(delivery_id):
    return 'runner:' + delivery_id


def _here():
    import socket
    return f'{socket.gethostname() or "unknown"}:{os.getpid()}:run'


def short_lived(argv, env):
    """One-shot queries, kanban workers and cpipe's own workers must not host or resume runs."""
    return bool(env.get('HERMES_KANBAN_TASK')) or any(
        a in {'-z', '--oneshot', '-q', '--query', '--query-file'} or a.startswith(('--oneshot=', '--query=', '--query-file='))
        for a in argv[1:])


def _short_lived():
    import sys
    return short_lived(sys.argv, os.environ)


def _record(key, value=None, delete=False):
    """Read, write or delete one row of cpipe's own intent table."""
    with scope() as (service, _, _home):
        db = service.journal.db
        if delete:
            db.execute('DELETE FROM intent WHERE id=?', (key,))
        elif value is not None:
            db.execute('INSERT OR REPLACE INTO intent(id,payload,acknowledged) VALUES (?,?,1)', (key, encoded(value)))
        else:
            row = db.execute('SELECT payload FROM intent WHERE id=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None


def notify(delivery_id, chat_key, text):
    """Keep the message until the chat accepts it; the watchdog and hooks retry delivery."""
    try:
        _record('notice:' + delivery_id, dict(chat_key=chat_key, text=text, at=int(time.time())))
    except Exception:
        log.exception('cpipe: could not store the message for %s', delivery_id)
    deliver()


def deliver():
    try:
        with scope() as (service, _, _home):
            rows = service.journal.db.execute("SELECT id, payload FROM intent WHERE id LIKE 'notice:%'").fetchall()
        for key, payload in rows:
            notice = json.loads(payload)
            try:
                sent = _CTX is not None and _CTX.inject_message(notice['text'], role='user', session_key=notice['chat_key'])
            except Exception:
                sent = False
            if sent:
                _record(key, delete=True)
                log.info('cpipe: delivered the message for %s', key[len('notice:'):])
            else:
                log.warning('cpipe: the chat for %s is not reachable yet; retrying later', key[len('notice:'):])
    except Exception:
        log.exception('cpipe: message delivery failed')


# How long a /cp-run receipt can start new deliveries in its chat after it was last used.
RUN_RECEIPT = 86400
SHOW = '[cpipe] Show the user this message exactly as written, then stop. Do not call tools.\n\n'


def _settled(delivery_id, result, error):
    result = result or {}
    phase = result.get('phase', '')
    head = HEADLINES.get(phase, 'stopped unexpectedly' + (f' ({error})' if error else ''))
    unresolved = sum(f['blocking'] for f in result.get('findings', ()))
    checks = result.get('checks') or {}
    if phase == 'waiting' and 'review' in checks and all(v.startswith('pass') for v in checks.values()):
        head = 'verified and reviewed, but its draft pull request could not be opened (see recoveries)'
    built = {k: v for k, v in checks.items() if k != 'review'}
    if phase == 'stopped' and built and all(v.startswith('pass') for v in built.values()):
        head = 'implemented and its checks pass, but its time ran out before the review finished'
    if phase == 'locally_ready' and unresolved:
        head = (f'ready for your review with {unresolved} unresolved review finding(s): checks pass, but '
                'more fix rounds kept trading one finding for another')
    return head, SHOW + report(delivery_id, head, result)


def report(delivery_id, head, result):
    """The settled delivery as markdown, rendered by code so the chat shows it without rewriting it."""
    tokens, steps = result.get('tokens') or {}, result.get('steps') or ()
    rows = [('Goal', result.get('goal')), ('Pull request', result.get('pull_request')),
            ('Worktree', result.get('worktree') and '`' + result['worktree'] + '`'),
            ('Steps', ', '.join(f'{k} ×{n}' if n > 1 else k for k, n in Counter(s['activity'] for s in steps).items())),
            ('Review rounds', result.get('review_cycles', 0)),
            ('Tokens', tokens and f"{tokens.get('total', 0):,} in {tokens.get('steps', 0)} steps "
                       f"({tokens.get('cache_read', 0) * 100 // max(1, tokens.get('total', 0))}% cache reads, billed at "
                       f"the provider's cache rate; {tokens.get('output', 0):,} output)")]
    lines = [f'**cpipe · `{delivery_id}` is {head}.**', '', '| | |', '|---|---|']
    lines += [f'| {k} | {_cell(v)} |' for k, v in rows if v not in (None, '', ())]
    if checks := result.get('checks'):
        lines += ['', '| Check | Result |', '|---|---|'] + [f'| {_cell(k)} | {_cell(v)} |' for k, v in checks.items()]
    if findings := result.get('findings'):
        lines += ['', '| Finding | Severity | Blocking | Detail |', '|---|---|---|---|']
        lines += [f"| {_cell(f['id'])} | {_cell(f['severity'])} | {'yes' if f['blocking'] else 'advice'} | "
                  f"{_cell(f['text'])}" + (f" (builder disputes: {_cell(f['builder_disputes'])})"
                                           if f.get('builder_disputes') else '') + ' |' for f in findings]
    if result.get('recoveries'):
        lines += ['', '**What happened on the way**'] + [f'- {_cell(r)}' for r in result['recoveries']]
    if result.get('phase') not in {'locally_ready', 'completed', 'cancelled'} or any(
            f['blocking'] for f in findings or ()):
        lines += ['', 'Fix the cause if needed and say **continue**: it picks up where it stopped '
                      '(asking approval again only for more time).']
    return '\n'.join(lines)


def launch(delivery_id, *, session_id, profile_id, chat_key, wait=0):
    """Run an approved delivery in the background; message the chat once it settles."""
    with scope() as (service, _, _home):
        controller.ready(service, delivery_id, session_id, profile_id)  # refuse before anything starts
        state = service.read(delivery_id)
        for (key,) in service.journal.db.execute("SELECT id FROM intent WHERE id LIKE 'runner:%'").fetchall():
            other = service.read(key[len('runner:'):])
            if (state and key != _runner(delivery_id) and other and other.approved
                    and other.activity in controller.ACTIVE_STEPS and other.plan.worktree == state.plan.worktree):
                raise ValueError('another cpipe run is working in this worktree; wait for it or stop it first')
        # Lets the watchdog or a later Hermes process resume the run if this one dies (see resume_orphans).
        previous = _record(_runner(delivery_id)) or {}
        _record(_runner(delivery_id), dict(previous, claimer=_here(), session_id=session_id, profile_id=profile_id,
                                           chat_key=chat_key, retry_at=0))
    lock, box = threading.Lock(), {}

    def work():
        result, error, stalls = None, '', 0

        def progress():
            try:
                with scope() as (service, _, _home):
                    state = controller.owned(service, delivery_id, session_id, profile_id)
                    return state.version, state.activity in controller.ACTIVE_STEPS and state.approved
            except ValueError:
                return None, False  # gone or not ours
            except Exception:
                return None, True  # unreadable for now: treat as still running
        log.info('cpipe: run %s started', delivery_id)
        # Keep going while the run moves forward; park it when it crashes again and again in place.
        while True:
            before, _ = progress()
            try:
                with scope() as (service, _, home):
                    result = controller.run(service, delivery_id, session_id=session_id, profile_id=profile_id,
                                            evidence_root=home / 'cpipe-deliveries' / 'evidence')
                error = ''
            except Exception as exc:
                # cpipe's own ValueErrors are static labels; anything else stays a type name.
                error = type(exc).__name__ + (': ' + str(exc)[:200] if type(exc) is ValueError else '')
                log.warning('cpipe: run %s crashed: %s', delivery_id, error, exc_info=True)
            after, active = progress()
            if not active:
                break  # settled: done, waiting, stuck, out of budget or cancelled
            stalls = 0 if after is not None and after != before else stalls + 1
            if stalls > CRASH_RETRIES:
                try:
                    record = _record(_runner(delivery_id))
                    if record:  # the watchdog tries again later, until it works, the budget ends or /cp-stop
                        record.update(retry_at=int(time.time()) + PARK, error=error, parks=record.get('parks', 0) + 1)
                        _record(_runner(delivery_id), record)
                        log.warning('cpipe: run %s parked for %ss after repeated crashes', delivery_id, PARK)
                        controller.tell(session_id, delivery_id, f'hit a problem; retrying every {PARK // 60} minutes')
                        if record['parks'] == 1:
                            notify(delivery_id, chat_key, SHOW + (
                                   f'**cpipe · `{delivery_id}` hit a problem it could not fix right away.**\n\n'
                                   f'| | |\n|---|---|\n| Error | {_cell(error or "unknown")} |\n'
                                   f'| Next | retries on its own every {PARK // 60} minutes until it works or its '
                                   f'budget ends |\n| Stop it | `/cp-stop {delivery_id}` |'))
                        return
                except Exception:
                    log.exception('cpipe: could not park %s', delivery_id)
                break
            time.sleep(RETRY_PAUSE * stalls)
        try:
            with scope() as (service, _, _home):
                state = controller.owned(service, delivery_id, session_id, profile_id)
                result = {**(result or {}), **controller.summary(state, service), 'worktree': state.plan.worktree, 'goal': state.plan.goal}
        except Exception:
            pass
        try:
            _record(_runner(delivery_id), delete=True)
        except Exception:
            log.exception('cpipe: could not clear the runner record of %s', delivery_id)
        head, text = _settled(delivery_id, result, error)
        log.info('cpipe: run %s is %s', delivery_id, head)
        controller.tell(session_id, delivery_id, f'{delivery_id} is {head}', done=True)
        with lock:
            if not box.get('detached'):
                box['result'] = dict(result or {}, message=f'The run is already {head}.')
                return
        notify(delivery_id, chat_key, text)
    # Copy the session/profile context into the thread.
    thread = threading.Thread(target=contextvars.copy_context().run, args=(work,),
                              name='cpipe-' + delivery_id, daemon=True)
    _RUNS[delivery_id] = thread
    thread.start()
    thread.join(wait)
    with lock:
        box['detached'] = True
        if 'result' in box:
            return box['result']
    return dict(delivery_id=delivery_id, phase='running',
                message='Building in the background. This chat gets a message when it is done; nothing to watch.')


_SCANNED = [0.0]


def resume_orphans(**_):
    """Hook and watchdog: relaunch runs whose host died or whose retry is due; redeliver messages."""
    import socket
    if _short_lived() or time.time() - _SCANNED[0] < WATCH - 5:
        return None
    _SCANNED[0] = time.time()
    from .delivery.native_bridge import alive
    try:
        with scope() as (service, _, _home):
            rows = service.journal.db.execute("SELECT id, payload FROM intent WHERE id LIKE 'runner:%'").fetchall()
        for key, payload in rows:
            record, delivery_id = json.loads(payload), key[len('runner:'):]
            thread = _RUNS.get(delivery_id)
            if thread and thread.is_alive() or record.get('retry_at', 0) > time.time():
                continue
            # A claim from this very process is only alive while its thread is.
            if record['claimer'] != _here() and alive(record['claimer']) and not record.get('retry_at'):
                continue
            with scope() as (service, _, _home):  # compare-and-swap: one process resumes it
                if service.journal.db.execute('UPDATE intent SET payload=? WHERE id=? AND payload=?',
                        (encoded({**record, 'claimer': _here()}), key, payload)).rowcount != 1:
                    continue
            log.info('cpipe: resuming run %s', delivery_id)
            try:
                launch(delivery_id, session_id=record['session_id'], profile_id=record['profile_id'],
                       chat_key=record['chat_key'])
            except ValueError as exc:  # no longer runnable (finished, cancelled, expired)
                _record(key, delete=True)
                log.info('cpipe: run %s not resumed: %s', delivery_id, exc)
                try:
                    with scope() as (service, _, _home):
                        state = controller.owned(service, delivery_id, record['session_id'], record['profile_id'])
                        result = {**controller.summary(state, service), 'worktree': state.plan.worktree, 'goal': state.plan.goal}
                    notify(delivery_id, record['chat_key'], _settled(delivery_id, result, str(exc)[:200])[1])
                except Exception:
                    pass
    except Exception:
        log.exception('cpipe: resume scan failed')  # never disturb the user's turn
    deliver()
    return None


def ensure_running(delivery_id, *, session_id, profile_id, chat_key, wait=0):
    """A repeated build: restart the run if nothing works on it any more, then report its real state."""
    with scope() as (service, _, _home):
        state = controller.owned(service, delivery_id, session_id, profile_id)
    if state.approved and controller.resumable(state):
        with scope() as (service, _, _home):
            state = controller.resume(service, delivery_id, session_id=session_id, profile_id=profile_id)
        log.info('cpipe: run %s resumed from %s', delivery_id, 'approval' if not state.approved else state.activity)
    thread, record = _RUNS.get(delivery_id), _record(_runner(delivery_id))
    if state.approved and state.activity in controller.ACTIVE_STEPS and not (thread and thread.is_alive()):
        if not record:
            return launch(delivery_id, session_id=session_id, profile_id=profile_id, chat_key=chat_key, wait=wait)
        if record.get('retry_at'):
            _record(_runner(delivery_id), {**record, 'retry_at': 1})  # retry a parked run now
        _SCANNED[0] = 0.0
        resume_orphans()  # takes over a dead or parked run exactly once
    with scope() as (service, _, _home):
        return _status(service, controller.owned(service, delivery_id, session_id, profile_id))


def watchdog():
    """Resume dead or parked runs and redeliver messages even when nobody is chatting."""
    def watch():
        while True:
            time.sleep(WATCH)
            resume_orphans()
    if not _short_lived() and not any(t.name == 'cpipe-watchdog' for t in threading.enumerate()):
        threading.Thread(target=contextvars.copy_context().run, args=(watch,),
                         name='cpipe-watchdog', daemon=True).start()


def _status(service, state):
    """The delivery summary plus whether anything is actually working on it."""
    out = controller.summary(state, service)
    out['goal'] = state.plan.goal
    record = _record(_runner(state.plan.envelope.delivery_id))
    if record:
        thread = _RUNS.get(state.plan.envelope.delivery_id)
        out['background'] = ('running' if not record.get('retry_at') or thread and thread.is_alive() else
                             f"retrying at {time.strftime('%H:%M', time.localtime(record['retry_at']))} "
                             f"after: {record.get('error') or 'an error'}")
    elif state.approved and state.activity in controller.ACTIVE_STEPS:
        out['background'] = 'not running; the next Hermes chat resumes it if it can, otherwise run it again'
    notice = _record('notice:' + state.plan.envelope.delivery_id)
    if notice:
        out['undelivered_message'] = notice['text']
    return out


def _cell(value):
    return ' '.join(str(value).split()).replace('|', '\\|') or '-'


def markdown(statuses):
    """/cp-status and /cp-stop for people: one table row per delivery, then each one's open findings."""
    if not statuses:
        return 'No cpipe deliveries in this chat.'
    lines = ['| Delivery | Goal | State | Checks | Review rounds | Tokens |', '|---|---|---|---|---|---|']
    for s in statuses:
        state = HEADLINES.get(s['phase']) or (s['phase'] + (' · ' + s['activity'] if s.get('activity') else ''))
        if s['phase'] == 'proposal' and not s['approved']:
            state = 'waiting for your approval'
        if s.get('background'):
            state += ' (' + s['background'] + ')'
        checks = ', '.join(f'{name}: {result}' for name, result in s.get('checks', {}).items())
        tokens = s.get('tokens') or {}
        lines.append('| ' + ' | '.join(_cell(v) for v in (
            '`' + s['delivery_id'] + '`', s.get('goal', ''), state, checks, s.get('review_cycles', 0),
            f"{tokens.get('total', 0):,} in {tokens.get('steps', 0)} steps" if tokens else '')) + ' |')
    for s in statuses:
        if s.get('findings'):
            lines += ['', f"**Open findings in `{s['delivery_id']}`**"]
            lines += [f"- {f['id']} ({f['severity']}{', blocking' if f['blocking'] else ''}): {_cell(f['text'])}"
                      + (f" — builder disputes: {_cell(f['builder_disputes'])}" if f.get('builder_disputes') else '')
                      for f in s['findings']]
        if s.get('undelivered_message'):
            lines += ['', f"**Message not delivered for `{s['delivery_id']}`:** {_cell(s['undelivered_message'])}"]
    return '\n'.join(lines)


def _intent_key(token):
    return 'slash:' + hashlib.sha256(token.encode()).hexdigest()


def dispatch(ctx, name, raw=''):
    sid, key = session(), session_key()
    if not sid or not key:
        return 'cpipe: a live session is required; no work started.'
    op = name[3:]
    if op in {'run', 'spawn'} and not raw.strip():
        return f'Usage: /{name} <task>. No work started.'
    if op in {'status', 'stop'}:
        try:
            with scope() as (service, profile, _):
                if op == 'status' and not raw.strip():
                    rows = service.journal.db.execute('SELECT snapshot FROM delivery').fetchall()
                    states = [State.from_dict(json.loads(row[0])) for row in rows]
                    return markdown([_status(service, s) for s in states
                        if (s.plan.envelope.session_id, s.plan.envelope.profile_id) == (sid, profile)])
                if op == 'stop':
                    controller.cancel(service, raw.strip(), session_id=sid, profile_id=profile)
                return markdown([_status(service, controller.owned(service, raw.strip(), sid, profile))])
        except Exception as exc:
            return 'cpipe: state lookup failed: ' + type(exc).__name__
    if op not in {'run', 'plan', 'build', 'spawn'} or not hasattr(ctx, 'inject_message'):
        return 'cpipe: unsupported operation or native session injection unavailable. No work started.'
    from agent.redact import redact_sensitive_text
    from .runtime.envpolicy import Redactor
    import os
    roles_text = ''
    if op == 'run':
        from .delivery.approach import roles
        with scope() as (_, _profile, home):
            saved, usable = roles(home)
        if len(usable) < 2:
            return ('cpipe needs two Hermes profiles with a saved model and provider: one builds, a different one '
                    'reviews. Set them up in Hermes, then run /cp-run again; no work started.')
        offer = '; '.join(f'{n} ({m})' for n, m in sorted(usable.items()))
        roles_text = (f"Use builder {json.dumps(saved['builder'])} and reviewer {json.dumps(saved['reviewer'])} "
            "(the user's saved cpipe roles, in order). " if saved else
            'No cpipe roles are saved yet. Include in your one clarify question which profiles build and which '
            'different one reviews, offering only: ' + offer + '. The builder edits code: never offer a read-only or '
            'review-only profile as builder. Ask for builders and reviewers in order (the next takes over if one '
            'refuses or keeps failing); propose at least one fallback for each and the strongest model as first '
            'reviewer. The answer is remembered for later runs. ')
    token = secrets.token_urlsafe(24)
    with scope() as (service, profile, _):
        record = dict(kind='slash', operation=op, session_id=sid, session_key=key,
                      profile_id=profile, expires=time.time() + (RUN_RECEIPT if op == 'run' else 900), state='pending', result=None)
        service.journal.db.execute('INSERT INTO intent(id,payload,acknowledged) VALUES (?,?,1)',
                                  (_intent_key(token), encoded(record)))
    prompt = ('The user explicitly chose cpipe with /' + name + '. '
        'Resolve the complete request and exact repository/worktree from this session with tools. '
        'Preserve unrelated edits. Do not implement in the coordinator session, delegate, publish or merge. '
        'For plan, propose an exact local-only spec with the repository\'s own test commands and distinct saved Hermes '
        'builder/reviewer profiles; do not guess models/providers (the host resolves them). Ask native clarify '
        'only for genuine scope/owner ambiguity. seconds is a bounded 30..1800 integer. The check criteria '
        'are exact test names as the runner reports them, not prose. Do not put credentials in any field. '
        'Call cpipe_execute_intent using token ' + token + ', operation ' + op + '. '
        'A proposal returns approval_arguments: call cpipe_approve_plan with those exact '
        'arguments and ask its returned question and choices with clarify, unchanged. Human consent cannot be '
        'inferred. If the user answers Yes, the same plan receipt may execute build for its own delivery_id; '
        'otherwise leave it waiting. '
        'Build accepts only a same-session approved delivery_id, not a task_id. '
        'Report real state, not worker prose. Execution is unsandboxed and never merges.\n'
        'User request (redacted):\n' + Redactor(os.environ)(redact_sensitive_text(raw)))
    if op == 'run':
        prompt = ('The user explicitly chose cpipe with /cp-run. Resolve the actual task, repository and worktree '
            'from this session using tools. Read project instructions such as AGENTS.md and inspect only the code the '
            'task touches; use installed skills only when clearly relevant. If you have a code index or code-navigation tool, locate code with it before broad file reads or searches; then read only the lines you need. '
            'Do not alter user configuration, profiles, skills or plugins. ' + roles_text +
            'Do not guess models/providers: Hermes resolves them from the profiles. Preserve unrelated edits.\n'
            'Give each delivery a short spec title naming the change; start it with the task\'s own ID only if '
            'the task has one. Choose kind bug, feature or change; risk normal or high; depth focused or detailed. For bugs, investigate '
            'and reproduce/trace the cause before proposing a focused fix, not a speculative detailed plan. '
            'Features and high-risk or cross-cutting work require detailed design decisions, even when described as bugs. '
            'Small clear changes need only a short approach. Explain the chosen approach. Ask native clarify only '
            'when tools cannot resolve a genuine scope, owner or product ambiguity; do not ask a mandatory planning question. '
            'Investigation before approval is read-only; do not run unapproved project test commands or modify project files. '
            'Do not implement, publish or merge in the coordinator. If you can delegate to a subagent, hand it '
            'read-only research (finding code, tracing a cause) and ask for a short answer with paths and line numbers, '
            'not file contents. Do not create worktrees or branches: '
            'cpipe makes a new worktree for each delivery from base, so the user\'s checkout is never changed. '
            'If the request names several tasks, handle all of them in this run. Put related tasks, especially ones '
            'touching the same files, in one delivery; give each independent one its own delivery by adding '
            'task (a short label such as t2) to every call\'s arguments for it. Deliveries run side by side, each in '
            'its own worktree.\n'
            'Call cpipe_execute_intent using token ' + token + ', operation prepare, arguments {spec, approach, expected_revision: 0}. '
            'The tool schema describes the exact fields. Use real evidence, explicit paths/acceptance and the '
            'repository\'s own test commands (from its scripts, Makefile, CI config or docs), not invented tests or identities. '
            'seconds is a bounded execution budget 30..7200 (prefer 3600 for features); draft review time does not consume it. '
            'Do not put credentials in any field; replace them with [REDACTED].\n'
            'Present the returned preview_attachment (MEDIA: path) on its own line in the assistant reply. '
            'Herm renders a clickable file card with Open preview; ordinary file:// Markdown links may be disabled. '
            'The returned preview_link is only a fallback for clients that allow local-file links. The .md document is for Herm Open preview '
            'and comments. End the reply with exactly: "Review the preview and reply with any comments or changes, '
            'or say proceed when it looks right. Nothing has been changed in the project yet." Incorporate submitted '
            'comments using prepare with the current expected_revision, then present the new revision for review. '
            'Herm comments may say revise in place: for a cpipe-managed draft, instead create a new revision '
            'through prepare, leaving the reviewed file intact. Explain the new link. '
            'Never change the document directly: every edit needs a new tool-generated revision. Preview approval only '
            'means the approach looks good; Human consent cannot be inferred.\n'
            'When the user is ready, use operation plan with only {draft_revision: current_revision}. Call '
            'cpipe_approve_plan with the exact returned approval_arguments and ask its returned question and choices '
            'with clarify, unchanged; with several deliveries, ask all their questions in one clarify call. Only for a '
            'Yes, continue automatically using the SAME token, operation build, arguments {delivery_id: returned_delivery_id}. '
            'No: that delivery starts nothing. Any other answer is the user\'s changes: revise with prepare using the '
            'current expected_revision, plan again and ask again. If build says approval is required, call '
            'cpipe_approve_plan again and ask its question. Unanswered or dismissed questions start nothing. '
            'build returns within seconds and runs in the background, repairing failing or broken checks itself. If its '
            'phase is running, tell the user in one line that it started and they will get a message here when it is '
            'done; otherwise report the result it returned. Do not poll status. If the user later asks to continue, '
            'resume or finish this delivery, call build again with the same token and delivery_id at once: it restarts '
            'the run, or picks a stopped one up where it stopped, and returns its real state. If that result has '
            'approval_arguments, the budget ran out: ask for approval again the same way, then build again. '
            'Never run cpipe code or scripts yourself. '
            'If the user asked for a pull request (for example "open a PR" or "create a draft PR"), set spec draft_pr '
            'true and branch: a new branch name that follows the user\'s and the repository\'s conventions (their '
            'instructions and skills for branches and pull requests, the contributing guide, recent branch names on '
            'origin). Put every instruction the user gave about commits, the PR title or description into acceptance, '
            'so the published PR is verified against it. After the checks and review pass, the builder writes the '
            'commits, title and description following those conventions, cpipe pushes and opens the draft PR, and the '
            'reviewer verifies the published PR before the user is told it is done; the approval question shows this. '
            'Otherwise omit draft_pr and branch; the work stays local. Never push, open PRs or merge yourself. If the '
            'user asks for something cpipe cannot do (merging, deploying, deleting branches), say so in the approach '
            'instead of promising it. Execution is unsandboxed.\n'
            'Later in this chat, when the user asks for more or different work (another task, or a redo with a changed '
            'plan such as a new branch name), start it yourself: call prepare with the same token and a new task label '
            'in the arguments, then plan and ask for approval as above. Never ask the user to type /cp-run again; '
            'their approval of the new plan is still required.\n'
            'User request (redacted):\n' + Redactor(os.environ)(redact_sensitive_text(raw)))
    if op == 'spawn':
        prompt = ('The user chose /cp-spawn to hand tasks to new chats, each running /cp-run. Work out which tasks '
            'they mean from this conversation: a reference such as "task 1,2" or "the first two" points at a list '
            'earlier in this chat. If a reference is ambiguous or matches nothing, ask with clarify. Do not start the '
            'work here. Write one brief per task; put tasks that touch the same files in one brief. The new chat cannot '
            'see this conversation, so each brief stands alone: repository path and base branch, the goal, the '
            'evidence as found earlier (file paths, line numbers, findings), acceptance criteria, what must not '
            'change, and whether the user asked for a pull request. Start each brief with "Title: " and the chat title. Use tools only to fill a gap the conversation '
            'leaves. Do not put credentials in any field; replace them with [REDACTED].\n'
            'Call cpipe_execute_intent using token ' + token + ', operation spawn, arguments '
            '{tasks: [{title, cwd, brief}]}. The title names the chat in the sidebar: make it specific, the change and '
            'where, such as "Fix path traversal in the attachment download route" (at most 80 characters), never '
            '"Task 1". Each task opens a new chat that asks the user to approve its plan there. '
            'Report the result in one short table; do not poll the chats.\n'
            'User reference (redacted):\n' + Redactor(os.environ)(redact_sensitive_text(raw)))
    try:
        queued = ctx.inject_message(prompt, role='user', session_key=key)
    except Exception:
        queued = False
    if not queued:
        with scope() as (service, _, __):
            service.journal.db.execute('DELETE FROM intent WHERE id=?', (_intent_key(token),))
        return 'cpipe: unable to inject into this session. Enable the native injection permission; no work started.'
    return 'cpipe: /' + name + ' queued in this session.'


def _spawn_target(payload):
    """The tasks and the calling desktop chat, checked before the receipt is claimed so a bad call can be retried."""
    tasks = payload.get('tasks') if set(payload) == {'tasks'} else None
    if (type(tasks) is not list or not 1 <= len(tasks) <= 8 or any(
            type(t) is not dict or set(t) - {'title', 'cwd', 'brief'} or type(t.get('title')) is not str
            or not 0 < len(t['title'].strip()) <= 80 or type(t.get('brief')) is not str or not t['brief'].strip()
            or type(t.get('cwd', '')) is not str for t in tasks)):
        raise ValueError('spawn takes tasks: 1 to 8 of {title (1-80 characters), cwd, brief}')
    import sys
    from gateway.session_context import get_session_env
    server = sys.modules.get('tui_gateway.server')
    caller = server._sessions.get(get_session_env('HERMES_UI_SESSION_ID') or '') if server else None
    if caller is None:
        raise ValueError('spawn works only from a chat in the Hermes desktop app')
    return tasks, server, caller


def spawn(tasks, server, caller):
    """Open one desktop chat per task and start /cp-run there with the task's brief, so each plan is approved
    in its own visible chat. Mirrors how the desktop starts a chat from a tool call."""
    from hermes_constants import profile_name_for_home
    from tui_gateway.transport import bind_transport, reset_transport
    started = []
    bound = bind_transport(caller.get('transport'))
    try:
        with server._session_profile_runtime_scope({'profile_home': caller.get('profile_home')}):
            for task in tasks:
                row = {'title': task['title'].strip()}
                created = server._create_session(None, {
                    'profile': profile_name_for_home(caller.get('profile_home')) or '', 'source': caller.get('source'),
                    'title': row['title'], 'cwd': task.get('cwd') or caller.get('cwd') or ''})
                if 'error' in created:
                    started.append({**row, 'status': 'failed', 'reason': created['error']['message']})
                    continue
                sid = created['result']['session_id']
                ran = server._methods['slash.exec'](None, {'session_id': sid, 'command': '/cp-run ' + task['brief'].strip()})
                said = (ran.get('result') or {}).get('output') or (ran.get('error') or {}).get('message') or ''
                if 'queued' in said:
                    started.append({**row, 'status': 'started', 'session_id': created['result']['stored_session_id']})
                else:
                    server._methods['session.close'](None, {'session_id': sid})
                    started.append({**row, 'status': 'failed', 'reason': said[:300]})
    finally:
        reset_transport(bound)
    return {'chats': started, 'next': 'Each started chat investigates its task and asks for approval there.'}


def execute(args, **kwargs):
    try:
        if type(args) is not dict or set(args) != {'token', 'operation', 'arguments'}:
            raise ValueError('invalid intent arguments')
        token, op, payload = args['token'], args['operation'], args['arguments']
        if type(token) is not str or not token or op not in {'prepare', 'plan', 'build', 'status', 'stop', 'spawn'} or type(payload) is not dict:
            raise ValueError('invalid intent arguments')
        sid, key = session(kwargs.get('session_id')), session_key()
        target = _spawn_target(payload) if op == 'spawn' else None
        with scope() as (service, profile, home):
            db = service.journal.db
            row = db.execute('SELECT payload FROM intent WHERE id=?', (_intent_key(token),)).fetchone()
            run_record = json.loads(row[0]) if row else None
            if run_record and run_record.get('operation') == 'run':
                run_key = _intent_key(token)
                if 'task' in payload:  # one /cp-run, several independent deliveries: one sub-receipt each
                    payload = dict(payload)
                    task = payload.pop('task')
                    if type(task) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,31}', task):
                        raise ValueError('task must be a short lowercase label such as t2')
                    if (run_record.get('kind') != 'slash' or run_record['expires'] <= time.time() or
                            (run_record['session_id'], run_record['profile_id'], run_record['session_key']) != (sid, profile, key)):
                        raise ValueError('missing, expired or wrong-session run receipt')
                    run_key += '-' + task
                    row = db.execute('SELECT payload FROM intent WHERE id=?', (run_key,)).fetchone()
                    if row is None:
                        run_record = {k: run_record[k] for k in (
                            'kind', 'operation', 'session_id', 'session_key', 'profile_id', 'expires')}
                        run_record.update(state='pending', result=None)
                        if op != 'prepare':
                            raise ValueError('start a task with prepare')
                        db.execute('INSERT OR IGNORE INTO intent(id,payload,acknowledged) VALUES (?,?,1)', (run_key, encoded(run_record)))
                    else:
                        run_record = json.loads(row[0])
                # The receipt's expiry bounds starting work; a frozen delivery can always be continued.
                lapsed = run_record['expires'] <= time.time() and not (
                    op in {'build', 'status'} and run_record.get('delivery_id'))
                if (run_record.get('kind') != 'slash' or lapsed
                    or (run_record['session_id'], run_record['profile_id'], run_record['session_key']) != (sid, profile, key)):
                    raise ValueError('missing, expired or wrong-session run receipt')
                base = json.loads(db.execute('SELECT payload FROM intent WHERE id=?', (_intent_key(token),)).fetchone()[0])
                if base['expires'] - time.time() < RUN_RECEIPT / 2:  # in use: follow-ups in this chat need no new /cp-run
                    base = dict(base, expires=time.time() + RUN_RECEIPT)
                    db.execute('UPDATE intent SET payload=? WHERE id=?', (encoded(base), _intent_key(token)))
                    if run_key == _intent_key(token):
                        run_record = base
                from .delivery.approach import execute as execute_run
                return json.dumps(execute_run(service, run_record, run_key, op, payload,
                    home=home, session_id=sid, profile_id=profile,
                    launch=lambda d, again=False: (ensure_running if again else launch)(
                        d, session_id=sid, profile_id=profile, chat_key=key, wait=SETTLE)))
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT payload FROM intent WHERE id=?', (_intent_key(token),)).fetchone()
                record = json.loads(row[0]) if row else None
                if (not record or record.get('kind') != 'slash' or record['expires'] <= time.time()
                    or (record['session_id'], record['profile_id'], record['session_key']) != (sid, profile, key)):
                    raise ValueError('missing, expired or wrong-session receipt')
                continuation = (record['operation'] == 'plan' and op == 'build' and record['state'] == 'complete'
                    and record['result'] and payload == {'delivery_id': record['result'].get('delivery_id')})
                if op not in {record['operation'], 'status'} and not continuation:
                    raise ValueError('incompatible receipt')
                if op != 'status':
                    if record['state'] == 'complete' and not continuation:
                        db.execute('COMMIT')
                        return json.dumps(record['result'])
                    if record['state'] != 'pending' and not continuation:
                        raise ValueError('intent already claimed; inspect status')
                    if continuation:
                        record['operation'] = 'build'
                    record['state'] = 'claimed'
                    db.execute('UPDATE intent SET payload=? WHERE id=?', (encoded(record), _intent_key(token)))
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
            from agent.redact import redact_sensitive_text
            from .runtime.envpolicy import Redactor
            import os
            material = encoded(payload)
            if redact_sensitive_text(material) != material or Redactor(os.environ)(material) != material:
                raise ValueError('credentials must be replaced with [REDACTED] before constructing a plan')
            if op == 'spawn':
                out = spawn(*target)
            elif op == 'plan':
                out = controller.summary(controller.from_spec(service, payload, session_id=sid, profile_id=profile))
            else:
                if set(payload) != {'delivery_id'} or type(payload['delivery_id']) is not str:
                    raise ValueError('only delivery_id is accepted')
                if op == 'build':
                    out = launch(payload['delivery_id'], session_id=sid, profile_id=profile, chat_key=key, wait=SETTLE)
                elif op == 'stop':
                    out = controller.cancel(service, payload['delivery_id'], session_id=sid, profile_id=profile)
                else:
                    out = _status(service, controller.owned(service, payload['delivery_id'], sid, profile))
            if op != 'status':
                record.update(state='complete', result=out)
                db.execute('UPDATE intent SET payload=? WHERE id=?', (encoded(record), _intent_key(token)))
            return json.dumps(out)
    except Exception as exc:
        # Never retain parameters, provider output or credential-bearing errors.
        # cpipe's own ValueErrors carry only static labels, so the coordinator
        # may see them and fix its spec; anything raised elsewhere stays opaque.
        tb = exc.__traceback__
        while tb and tb.tb_next:
            tb = tb.tb_next
        own = (type(exc) is ValueError and tb is not None
               and Path(tb.tb_frame.f_code.co_filename).is_relative_to(Path(__file__).parent))
        return json.dumps({'error': 'cpipe operation failed: ' + type(exc).__name__ +
            (' (' + str(exc)[:300] + ')' if own else '') +
            (' cpipe was updated while this chat was open; start a new chat.' if isinstance(exc, ImportError) else '') +
            '. No authority inferred; inspect the current draft or /cp-status before retrying.'})


def register(ctx):
    global _CTX
    _CTX = ctx
    from . import plan_approval
    plan_approval.register(ctx)
    ctx.register_tool(name=SCHEMA['name'], toolset='cpipe', schema=SCHEMA, handler=execute)
    # Load every module a run needs now, so a plugin update mid-run never mixes versions.
    import importlib
    for name in ('approach', 'approval', 'artifact', 'check_runner', 'contracts', 'hermes_worker', 'live_probe',
                 'native_bridge', 'review', 'workers'):
        importlib.import_module('.delivery.' + name, __package__)
    for name in ('routing', 'runtime.envpolicy', 'runtime.launcher'):
        importlib.import_module('.' + name, __package__)
    if hasattr(ctx, 'register_hook'):
        for hook in ('on_session_start', 'pre_llm_call'):
            ctx.register_hook(hook, resume_orphans)
    watchdog()
    if hasattr(ctx, 'register_command'):
        for name, about, hint in (
                ('cp-run', 'Run a task with adaptive investigation and review', '<task>'),
                ('cp-spawn', 'Start tasks from this chat in new chats, each with /cp-run', '<which tasks>'),
                ('cp-status', 'cpipe: status', '[delivery_id]'), ('cp-stop', 'cpipe: stop', '<delivery_id>')):
            ctx.register_command(name, lambda raw='', _name=name: dispatch(ctx, _name, raw),
                description=about, args_hint=hint,
                argument_mode='text')
