"""Human consent for frozen delivery envelopes, given through Hermes clarify."""
import json
import logging
import re

NAME = 'cpipe_approve_plan'
SCHEMA = dict(name=NAME, description='Get the approval question for the exact frozen envelope. '
    'Use delivery_id and plan_revision from the proposal in its originating session, then ask the returned '
    'question and choices with clarify, unchanged. Only the user\'s own clarify answer approves; consent is never '
    'supplied by the model. Approval does not itself launch workers or authorize publication.',
    parameters=dict(type='object', additionalProperties=False,
        properties=dict(delivery_id=dict(type='string'), plan_revision=dict(type='string')),
        required=['delivery_id', 'plan_revision']))
_IDS = re.compile(r'^(Delivery|Plan revision): (\S+)$', re.M)
log = logging.getLogger(__name__)


def _scope():
    from hermes_constants import get_hermes_home
    from hermes_cli.profiles import get_active_profile_name
    from .delivery.journal import Journal
    return Journal.for_profile(get_hermes_home()), get_active_profile_name()


def request(args, session_id=None):
    from .native import session
    from .delivery.service import Service
    from .delivery.approval import request as approve
    sid = session(session_id)
    if not sid:
        return {'error': 'Approval requires its originating session.'}
    journal, profile = _scope()
    with journal:
        return approve(Service(journal), args, session_id=sid, profile_id=profile)


def observe(tool_name='', args=None, result=None, session_id='', **_):
    """post_tool_call: record the user's answer to a cpipe approval question asked through clarify.
    The answer comes from the host's clarify result, which the model cannot write."""
    if tool_name != 'clarify' or not session_id:
        return
    try:
        reply = json.loads(result) if isinstance(result, str) else result
        if not isinstance(reply, dict) or reply.get('outcome') != 'submitted':
            return
        from .delivery.service import Service
        from .delivery.approval import answer
        for asked, response in zip((args or {}).get('questions') or [], reply.get('responses') or []):
            text = str(asked.get('question') or '') if isinstance(asked, dict) else ''
            ids = dict(_IDS.findall(text))
            if len(ids) != 2 or response.get('status') != 'answered':
                continue
            journal, profile = _scope()
            with journal:
                answer(Service(journal), {'delivery_id': ids['Delivery'], 'plan_revision': ids['Plan revision']},
                       session_id=session_id, profile_id=profile, asked=text, response=response.get('user_response'))
    except Exception as exc:
        log.warning('cpipe: approval answer not recorded (%s)', type(exc).__name__)


# A reply that only says to go ahead, such as "proceed", "looks good, go ahead" or "lgtm, start it".
# Anything else (a question, a change, a bare "yes" to some other question) is not consent.
_GO = {'proceed', 'go', 'start', 'begin', 'build', 'approve', 'approved', 'lgtm', 'ship'}
_FILLER = {'ok', 'okay', 'yes', 'yep', 'yeah', 'sure', 'looks', 'good', 'great', 'perfect', 'fine', 'sounds',
           'ahead', 'it', 'do', 'lets', 'let', 's', 'please', 'thanks', 'thank', 'you', 'now', 'with', 'that',
           'this', 'the', 'plan', 'and', 'all', 'right', 'alright'}


def consent(session_id='', user_message=None, **_):
    """pre_llm_call: the user's own chat message saying to go ahead approves the draft revisions this chat
    was shown before it. The host passes the user's text; the model cannot write it."""
    if not session_id or type(user_message) is not str or len(user_message) > 80:
        return
    words = re.findall(r"[a-z]+", user_message.casefold())
    if not words or not _GO & set(words) or not set(words) <= _GO | _FILLER:
        return
    try:
        from .delivery.journal import encoded
        journal, _ = _scope()
        with journal:
            db = journal.db
            for key, payload in db.execute(
                    "SELECT id, payload FROM intent WHERE id LIKE 'slash:%' AND json_extract(payload, '$.session_id')=? "
                    "AND json_extract(payload, '$.state') IN ('draft', 'proposal')", (session_id,)).fetchall():
                record = json.loads(payload)
                db.execute('UPDATE intent SET payload=? WHERE id=? AND payload=?',
                           (encoded(dict(record, consent=record['draft_revision'])), key, payload))
    except Exception as exc:
        log.warning('cpipe: proceed not recorded (%s)', type(exc).__name__)


def register(ctx):
    from .live import entry, module

    def handler(args, **kwargs):
        try:
            return json.dumps(module('plan_approval').request(args, kwargs.get('session_id')))
        except Exception as exc:
            return json.dumps({'error': 'Approval failed: ' + type(exc).__name__ +
                               '. No consent inferred; inspect the proposal before retrying.'})
    ctx.register_tool(name=NAME, toolset='cpipe', schema=SCHEMA, handler=handler)
    if hasattr(ctx, 'register_hook'):
        ctx.register_hook('post_tool_call', entry('plan_approval', 'observe'))
        ctx.register_hook('pre_llm_call', entry('plan_approval', 'consent'))
