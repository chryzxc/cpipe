"""Session-bound task commands with explicit frozen-plan consent."""
from contextlib import contextmanager
import contextvars
import hashlib
import json
from pathlib import Path
import secrets
import threading
import time

from .delivery.journal import Journal, encoded
from .delivery.service import Service, State
from .delivery import controller

SCHEMA = dict(name='cpipe_execute_intent', description='Optional cpipe workflow. Consume an explicit /cp-run command receipt. '
    'Ordinary requests use normal tools. Plans require explicit worktree, scope, test-command checks and distinct saved '
    'builder/reviewer profiles. Prepare a reviewable Markdown draft, then freeze and approve before build. Never publishes or merges.',
    parameters=dict(type='object', additionalProperties=False,
        properties=dict(token=dict(type='string'), operation=dict(type='string', enum=['prepare', 'plan', 'build', 'status', 'stop']),
            arguments=dict(type='object', description='prepare: {spec, approach, expected_revision} (0 initially; current revision for edits). '
                'approach: kind (bug|feature|change), depth (focused|detailed), risk (normal|high), summary; '
                'evidence, steps, decisions, risks are string lists. Bugs need evidence; feature/high-risk work needs detailed decisions. '
                'spec: worktree, repo, base, goal, acceptance, '
                'non_goals, write_paths, forbidden_paths, builder, reviewer, seconds and checks. Each check has '
                'obligation, argv (the repository\'s own test command as a list, run in the worktree, any language; '
                'argv[0] on PATH or absolute), timeout_seconds and criteria (test names). If argv contains {report}, '
                'it is replaced by a private JUnit XML path (e.g. --junitxml={report}, --reporter=junit '
                '--outputFile={report}) and each criterion matches one reported classname::name or name; otherwise '
                'pass/fail is the exit code and every criterion must appear in the output. Prefer {report}. '
                'Prefer commands that provision their own dependencies (uv run, npx, go test, cargo test); '
                'otherwise install first, e.g. ["sh","-c","npm ci && npx vitest run ..."]. '
                'Exactly one check has obligation behavior: the test proving the change; it must fail before implementing, '
                'and the builder writes it first, so its file must be inside write_paths. '
                'For /cp-run, plan: {draft_revision} freezes the reviewed draft; build: {delivery_id} from that plan after native approval. '
                'Run status: {}. Existing plan receipts accept their full spec. Other operations: only delivery_id.')),
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
CRASH_RETRIES = 2  # a crashed step is re-issued on a fresh card this many times

HEADLINES = {'locally_ready': 'done and ready for your review',
             'stopped': 'stopped: its time or turn budget ran out',
             'diagnosis': 'stopped: it got stuck (no valid failing test, or review findings it could not resolve)',
             'waiting': 'stopped: it needs you',
             'cancelled': 'cancelled'}


def launch(delivery_id, *, session_id, profile_id, chat_key):
    """Run an approved delivery in the background; message the chat once it settles."""
    with scope() as (service, _, _home):
        controller.ready(service, delivery_id, session_id, profile_id)  # refuse before anything starts
    def work():
        result, error = None, ''
        for _ in range(CRASH_RETRIES + 1):
            try:
                with scope() as (service, _, home):
                    result = controller.run(service, delivery_id, session_id=session_id, profile_id=profile_id,
                                            evidence_root=home / 'cpipe-deliveries' / 'evidence')
                error = ''
                break
            except Exception as exc:
                # cpipe's own ValueErrors are static labels; anything else stays a type name.
                error = type(exc).__name__ + (': ' + str(exc)[:200] if type(exc) is ValueError else '')
        try:
            with scope() as (service, _, _home):
                state = controller.owned(service, delivery_id, session_id, profile_id)
                result = {**(result or {}), **controller.summary(state, service), 'worktree': state.plan.worktree}
        except Exception:
            pass
        phase = (result or {}).get('phase', '')
        head = HEADLINES.get(phase, 'stopped unexpectedly' + (f' ({error})' if error else ''))
        text = (f'[cpipe] Delivery {delivery_id} is {head}.\n' + json.dumps(result or {}, sort_keys=True) +
                '\nTell the user this result in a few short lines: what was done, each check result, tokens used, '
                'and the worktree to review. If it stopped, say why and what they can do. Do not call cpipe tools.')
        try:
            if _CTX is not None:
                _CTX.inject_message(text, role='user', session_key=chat_key)
        except Exception:
            pass  # /cp-status still shows the result
    # Copy the session/profile context into the thread.
    thread = threading.Thread(target=contextvars.copy_context().run, args=(work,),
                              name='cpipe-' + delivery_id, daemon=True)
    _RUNS[delivery_id] = thread
    thread.start()
    return dict(delivery_id=delivery_id, phase='running',
                message='Building in the background. This chat gets a message when it is done; nothing to watch.')


def _intent_key(token):
    return 'slash:' + hashlib.sha256(token.encode()).hexdigest()


def dispatch(ctx, name, raw=''):
    sid, key = session(), session_key()
    if not sid or not key:
        return 'cpipe: a live session is required; no work started.'
    op = name[3:]
    if op == 'run' and not raw.strip():
        return 'Usage: /cp-run <task>. No work started.'
    if op in {'status', 'stop'}:
        try:
            with scope() as (service, profile, _):
                if op == 'status' and not raw.strip():
                    rows = service.journal.db.execute('SELECT snapshot FROM delivery').fetchall()
                    states = [State.from_dict(json.loads(row[0])) for row in rows]
                    return json.dumps({'deliveries': [controller.summary(s, service) for s in states
                        if (s.plan.envelope.session_id, s.plan.envelope.profile_id) == (sid, profile)]})
                if op == 'stop':
                    return json.dumps(controller.cancel(service, raw.strip(), session_id=sid, profile_id=profile))
                return json.dumps(controller.summary(controller.owned(service, raw.strip(), sid, profile), service))
        except Exception as exc:
            return json.dumps({'error': 'state lookup failed: ' + type(exc).__name__})
    if op not in {'run', 'plan', 'build'} or not hasattr(ctx, 'inject_message'):
        return 'cpipe: unsupported operation or native session injection unavailable. No work started.'
    from agent.redact import redact_sensitive_text
    from .runtime.envpolicy import Redactor
    import os
    token = secrets.token_urlsafe(24)
    with scope() as (service, profile, _):
        record = dict(kind='slash', operation=op, session_id=sid, session_key=key,
                      profile_id=profile, expires=time.time() + (86400 if op == 'run' else 900), state='pending', result=None)
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
        'A proposal returns the full frozen plan and approval_arguments: present it to the user and use '
        'cpipe_approve_plan with those exact arguments. Human consent cannot be inferred. If approved, '
        'the same plan receipt may execute build for its own delivery_id; otherwise leave it waiting. '
        'Build accepts only a same-session approved delivery_id, not a task_id. '
        'Report real state, not worker prose. This controller is local-only, unsandboxed and has no PR adapter.\n'
        'User request (redacted):\n' + Redactor(os.environ)(redact_sensitive_text(raw)))
    if op == 'run':
        prompt = ('The user explicitly chose cpipe with /cp-run. Resolve the actual task, repository and worktree '
            'from this session using tools. Read project instructions such as AGENTS.md and inspect only the code the '
            'task touches; use installed skills only when clearly relevant. '
            'Do not alter user configuration, profiles, skills or plugins. Do not guess models/providers: Hermes resolves '
            'saved distinct builder/reviewer profiles. Preserve unrelated edits.\n'
            'Choose kind bug, feature or change; risk normal or high; depth focused or detailed. For bugs, investigate '
            'and reproduce/trace the cause before proposing a focused fix, not a speculative detailed plan. '
            'Features and high-risk or cross-cutting work require detailed design decisions, even when described as bugs. '
            'Small clear changes need only a short approach. Explain the chosen approach. Ask native clarify only '
            'when tools cannot resolve a genuine scope, owner or product ambiguity; do not ask a mandatory planning question. '
            'Investigation before approval is read-only; do not run unapproved project test commands or modify project files. '
            'Do not implement, delegate, publish or merge in the coordinator.\n'
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
            'When the user is ready, use operation plan with only {draft_revision: current_revision}. Present the frozen '
            'scope and call cpipe_approve_plan with the exact returned approval_arguments. Only if its status is approved, '
            'continue automatically using the SAME token, operation build, arguments {delivery_id: returned_delivery_id}. '
            'No second slash command or copied ID is needed. Denial, unavailable approval or dismissal starts nothing. '
            'build returns at once and runs in the background, repairing failing or broken checks itself; tell the user '
            'in one line that it started and they will get a message here when it is done. Do not poll status. '
            'Execution is local-only, unsandboxed; no publication adapter.\n'
            'User request (redacted):\n' + Redactor(os.environ)(redact_sensitive_text(raw)))
    try:
        queued = ctx.inject_message(prompt, role='user', session_key=key)
    except Exception:
        queued = False
    if not queued:
        with scope() as (service, _, __):
            service.journal.db.execute('DELETE FROM intent WHERE id=?', (_intent_key(token),))
        return 'cpipe: unable to inject into this session. Enable the native injection permission; no work started.'
    return 'cpipe: /' + name + ' queued in this session.'


def execute(args, **kwargs):
    try:
        if type(args) is not dict or set(args) != {'token', 'operation', 'arguments'}:
            raise ValueError('invalid intent arguments')
        token, op, payload = args['token'], args['operation'], args['arguments']
        if type(token) is not str or not token or op not in {'prepare', 'plan', 'build', 'status', 'stop'} or type(payload) is not dict:
            raise ValueError('invalid intent arguments')
        sid, key = session(kwargs.get('session_id')), session_key()
        with scope() as (service, profile, home):
            db = service.journal.db
            row = db.execute('SELECT payload FROM intent WHERE id=?', (_intent_key(token),)).fetchone()
            run_record = json.loads(row[0]) if row else None
            if run_record and run_record.get('operation') == 'run':
                if (run_record.get('kind') != 'slash' or run_record['expires'] <= time.time()
                    or (run_record['session_id'], run_record['profile_id'], run_record['session_key']) != (sid, profile, key)):
                    raise ValueError('missing, expired or wrong-session run receipt')
                from .delivery.approach import execute as execute_run
                return json.dumps(execute_run(service, run_record, _intent_key(token), op, payload,
                    home=home, session_id=sid, profile_id=profile,
                    launch=lambda d: launch(d, session_id=sid, profile_id=profile, chat_key=key)))
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
            if op == 'plan':
                out = controller.summary(controller.from_spec(service, payload, session_id=sid, profile_id=profile))
            else:
                if set(payload) != {'delivery_id'} or type(payload['delivery_id']) is not str:
                    raise ValueError('only delivery_id is accepted')
                if op == 'build':
                    out = launch(payload['delivery_id'], session_id=sid, profile_id=profile, chat_key=key)
                elif op == 'stop':
                    out = controller.cancel(service, payload['delivery_id'], session_id=sid, profile_id=profile)
                else:
                    out = controller.summary(controller.owned(service, payload['delivery_id'], sid, profile), service)
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
    if hasattr(ctx, 'register_command'):
        for name in ('cp-run', 'cp-status', 'cp-stop'):
            ctx.register_command(name, lambda raw='', _name=name: dispatch(ctx, _name, raw),
                description='Run a task with adaptive investigation and review' if name == 'cp-run' else 'cpipe: ' + name[3:],
                args_hint='<task>' if name == 'cp-run' else '[delivery_id]' if name == 'cp-status' else '<delivery_id>',
                argument_mode='text')
