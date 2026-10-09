"""Session-bound slash intents for v2 only. No legacy cards, stages or daemons."""
from contextlib import contextmanager
import hashlib
import json
import secrets
import time

from .delivery.journal import Journal, encoded
from .delivery.service import Service, State
from .delivery import controller

SCHEMA = dict(name='cpipe_execute_intent', description='Optional cpipe v2 only. Consume an explicit /cp-* command receipt. '
    'Ordinary requests use normal tools. Plans require explicit worktree, scope, pytest checks and distinct saved '
    'builder/reviewer profiles. Build requires native human envelope approval. Never publishes or merges.',
    parameters=dict(type='object', additionalProperties=False,
        properties=dict(token=dict(type='string'), operation=dict(type='string', enum=['plan', 'build', 'status', 'stop']),
            arguments=dict(type='object', description='plan: exact v2 spec with worktree, repo, base, goal, acceptance, '
                'non_goals, write_paths, forbidden_paths, builder, reviewer, seconds and checks. Each check has '
                'obligation, targets (relative .py files), criteria (exact pytest classname::name), timeout_seconds. '
                'Other operations: only delivery_id. No legacy task_id.')),
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


def _intent_key(token):
    return 'slash-v2:' + hashlib.sha256(token.encode()).hexdigest()


def dispatch(ctx, name, raw=''):
    sid, key = session(), session_key()
    if not sid or not key:
        return 'cpipe v2: a live session is required; no work started.'
    op = name[3:]
    if op in {'status', 'stop'}:
        try:
            with scope() as (service, profile, _):
                if op == 'status' and not raw.strip():
                    rows = service.journal.db.execute('SELECT snapshot FROM delivery').fetchall()
                    states = [State.from_dict(json.loads(row[0])) for row in rows]
                    return json.dumps({'engine_version': 2, 'deliveries': [controller.summary(s) for s in states
                        if (s.plan.envelope.session_id, s.plan.envelope.profile_id) == (sid, profile)]})
                if op == 'stop':
                    return json.dumps(controller.cancel(service, raw.strip(), session_id=sid, profile_id=profile))
                return json.dumps(controller.summary(controller.owned(service, raw.strip(), sid, profile)))
        except Exception as exc:
            return json.dumps({'error': 'v2 state lookup failed: ' + type(exc).__name__})
    if op not in {'plan', 'build'} or not hasattr(ctx, 'inject_message'):
        return 'cpipe v2: unsupported operation or native session injection unavailable. No legacy fallback.'
    from agent.redact import redact_sensitive_text
    from .runtime.envpolicy import Redactor
    import os
    token = secrets.token_urlsafe(24)
    with scope() as (service, profile, _):
        record = dict(kind='slash_v2', operation=op, session_id=sid, session_key=key,
                      profile_id=profile, expires=time.time() + 900, state='pending', result=None)
        service.journal.db.execute('INSERT INTO intent(id,payload,acknowledged) VALUES (?,?,1)',
                                  (_intent_key(token), encoded(record)))
    prompt = ('The user explicitly chose cpipe v2 with /' + name + '. No legacy pipeline is supported. '
        'Resolve the complete request and exact repository/worktree from this session with tools. '
        'Preserve unrelated edits. Do not implement in the coordinator session, delegate, publish or merge. '
        'For plan, propose an exact local-only spec with explicit pytest checks and distinct saved Hermes '
        'builder/reviewer profiles; do not guess models/providers (the host resolves them). Ask native clarify '
        'only for genuine scope/owner ambiguity. seconds is a bounded 30..1800 integer. The check criteria '
        'are exact pytest classname::name identities, not prose. Do not put credentials in any field. '
        'Call cpipe_execute_intent using token ' + token + ', operation ' + op + '. '
        'A proposal returns the full frozen plan and approval_arguments: present it to the user and use '
        'cpipe_approve_plan with those exact arguments. Human consent cannot be inferred. If approved, '
        'the same plan receipt may execute build for its own delivery_id; otherwise leave it waiting. '
        'Build accepts only a same-session approved delivery_id, never a legacy task_id. '
        'Report real state, not worker prose. This controller is local-only, unsandboxed and has no PR adapter.\n'
        'User request (redacted):\n' + Redactor(os.environ)(redact_sensitive_text(raw)))
    try:
        queued = ctx.inject_message(prompt, role='user', session_key=key)
    except Exception:
        queued = False
    if not queued:
        with scope() as (service, _, __):
            service.journal.db.execute('DELETE FROM intent WHERE id=?', (_intent_key(token),))
        return 'cpipe v2: unable to inject into this session. Enable the native injection permission; no work started.'
    return 'cpipe v2: /' + name + ' queued in this session; no legacy workflow will run.'


def execute(args, **kwargs):
    try:
        if type(args) is not dict or set(args) != {'token', 'operation', 'arguments'}:
            raise ValueError('invalid v2 intent arguments')
        token, op, payload = args['token'], args['operation'], args['arguments']
        if type(token) is not str or not token or op not in {'plan', 'build', 'status', 'stop'} or type(payload) is not dict:
            raise ValueError('invalid v2 intent arguments')
        sid, key = session(kwargs.get('session_id')), session_key()
        with scope() as (service, profile, home):
            db = service.journal.db
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT payload FROM intent WHERE id=?', (_intent_key(token),)).fetchone()
                record = json.loads(row[0]) if row else None
                if (not record or record.get('kind') != 'slash_v2' or record['expires'] <= time.time()
                    or (record['session_id'], record['profile_id'], record['session_key']) != (sid, profile, key)):
                    raise ValueError('missing, expired or wrong-session v2 receipt')
                continuation = (record['operation'] == 'plan' and op == 'build' and record['state'] == 'complete'
                    and record['result'] and payload == {'delivery_id': record['result'].get('delivery_id')})
                if op not in {record['operation'], 'status'} and not continuation:
                    raise ValueError('incompatible v2 receipt')
                if op != 'status':
                    if record['state'] == 'complete' and not continuation:
                        db.execute('COMMIT')
                        return json.dumps(record['result'])
                    if record['state'] != 'pending' and not continuation:
                        raise ValueError('v2 intent already claimed; inspect status')
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
                    raise ValueError('only v2 delivery_id is accepted')
                if op == 'build':
                    out = controller.run(service, payload['delivery_id'], session_id=sid, profile_id=profile,
                                         evidence_root=home / 'cpipe-v2' / 'evidence')
                elif op == 'stop':
                    out = controller.cancel(service, payload['delivery_id'], session_id=sid, profile_id=profile)
                else:
                    out = controller.summary(controller.owned(service, payload['delivery_id'], sid, profile))
            if op != 'status':
                record.update(state='complete', result=out)
                db.execute('UPDATE intent SET payload=? WHERE id=?', (encoded(record), _intent_key(token)))
            return json.dumps(out)
    except Exception as exc:
        # Never retain parameters, provider output or credential-bearing errors.
        return json.dumps({'error': 'cpipe v2 operation failed: ' + type(exc).__name__ +
            '. No legacy fallback; inspect /cp-status before reissuing an intent.'})


def register(ctx):
    from . import plan_approval
    plan_approval.register(ctx)
    ctx.register_tool(name=SCHEMA['name'], toolset='cpipe', schema=SCHEMA, handler=execute)
    if hasattr(ctx, 'register_command'):
        for name in ('cp-plan', 'cp-build', 'cp-status', 'cp-stop'):
            ctx.register_command(name, lambda raw='', _name=name: dispatch(ctx, _name, raw),
                description='cpipe v2: ' + name[3:], args_hint='[request]' if name == 'cp-plan' else '<delivery_id>',
                argument_mode='text')
