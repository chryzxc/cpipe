"""Human consent for v2 envelopes only; there is no legacy task approval path."""
import json

NAME = 'cpipe_approve_plan'
SCHEMA = dict(name=NAME, description='Show native one-operation human approval for the exact frozen v2 envelope. '
    'Use delivery_id and plan_revision from the proposal in its originating session. '
    'Consent is never supplied by the model. Approval does not itself launch workers or authorize publication.',
    parameters=dict(type='object', additionalProperties=False,
        properties=dict(delivery_id=dict(type='string'), plan_revision=dict(type='string')),
        required=['delivery_id', 'plan_revision']))


def _no_user_can_answer():
    try:
        from tools import approval_context as context
        predicate = getattr(context, '_no_user_can_answer', None)
        if predicate is not None:
            return predicate() is not False
        return any(getattr(context, name)() is not False for name in (
            '_is_single_query_approval_context', '_is_cron_approval_context',
            '_is_unattended_platform_approval_context'))
    except Exception:
        return True


def _native_prompt(command, description, pattern, title):
    from tools import approval
    from tools.approval_context import _fire_approval_hook, get_current_session_key
    from tools.approval_gateway_wait import _await_gateway_decision
    from tools.approval_prompt import prompt_dangerous_approval
    if _no_user_can_answer():
        return dict(resolved=False, choice=None, unavailable=True)
    from agent.redact import redact_sensitive_text
    command, description = redact_sensitive_text(command), redact_sensitive_text(description)
    data = dict(command=command, description=description, pattern_key=pattern, pattern_keys=[pattern],
                allow_session=False, allow_permanent=False, title=title)
    key = get_current_session_key()
    with approval._lock:
        notify = approval._gateway_notify_cbs.get(key)
    if notify is not None:
        return _await_gateway_decision(key, notify, data)
    from tools.terminal_tool import _get_approval_callback
    callback = _get_approval_callback()
    if callback is None:
        return dict(resolved=False, choice=None, unavailable=True)
    hooks = {k: v for k, v in data.items() if k not in ('allow_session', 'allow_permanent')}
    hooks.update(session_key=key, surface='cli')
    _fire_approval_hook('pre_approval_request', **hooks)
    choice = prompt_dangerous_approval(command, description, allow_session=False,
                                      allow_permanent=False, title=title, approval_callback=callback)
    _fire_approval_hook('post_approval_response', **hooks, choice=choice)
    return dict(resolved=choice in ('once', 'deny'), choice=choice)


def request(args, session_id=None):
    from hermes_constants import get_hermes_home
    from hermes_cli.profiles import get_active_profile_name
    from .native import session
    from .delivery.journal import Journal
    from .delivery.service import Service
    from .delivery.approval import request as approve
    sid = session(session_id)
    if not sid:
        return {'error': 'Native v2 approval requires its originating session.'}
    with Journal.for_profile(get_hermes_home()) as journal:
        return approve(Service(journal), args, session_id=sid, profile_id=get_active_profile_name())


def register(ctx):
    def handler(args, **kwargs):
        try:
            return json.dumps(request(args, kwargs.get('session_id')))
        except Exception as exc:
            return json.dumps({'error': 'Native v2 approval failed: ' + type(exc).__name__ +
                               '. No consent inferred; inspect the proposal before retrying.'})
    ctx.register_tool(name=NAME, toolset='cpipe', schema=SCHEMA, handler=handler)
