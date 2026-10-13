"""Frozen envelope consent through Hermes clarify.

The model asks the exact question; only the user's answer, observed from the host's
clarify result, can approve. The journal remains the approval authority. Execution is
not sandboxed.
"""
import hashlib
import json
import time

from .artifact import validate_worktree
from .journal import encoded
from .service import Event, deadline


def arguments(state):
    revision = hashlib.sha256(encoded([
        state.plan.digest(), state.artifact, state.version, state.active_intent,
    ]).encode()).hexdigest()
    return {'delivery_id': state.plan.envelope.delivery_id, 'plan_revision': revision}


def _fresh(service, args, session_id, profile_id):
    state = service.read(args['delivery_id'])
    if state is None:
        raise ValueError('missing proposal')
    owner = state.plan.envelope
    from .approach import validate_document
    validate_document(service, state.plan)
    now = int(time.time())
    if (owner.session_id != session_id or owner.profile_id != profile_id
            or state.phase != 'proposal' or state.approved
            or state.activity != 'approve_envelope' or not state.active_intent
            or arguments(state) != args or now < state.last_at
            or now >= deadline(state)
            or validate_worktree(state.plan).digest != state.artifact):
        raise ValueError('unbound, changed or expired proposal')
    return state


def _key(args):
    return 'approval-ui:' + hashlib.sha256(encoded(args).encode()).hexdigest()


def _previous(db, key, session_id, profile_id):
    row = db.execute('SELECT payload FROM intent WHERE id=?', (key,)).fetchone()
    if row is None:
        return None
    record = json.loads(row[0])
    if record['session_id'] != session_id or record['profile_id'] != profile_id:
        raise ValueError('approval belongs to another session or profile')
    return record['result']


def presentation(plan, revision):
    """Readable copy from the frozen contract; no generated or inferred authority."""
    capabilities = plan.envelope.capabilities
    grants = {'local': 'Local development work',
              'push': f'Push the branch {plan.envelope.branch} to origin, after the checks and review pass',
              'draft_pr': f"Open a draft pull request against {plan.envelope.base.removeprefix('origin/')}",
              'mark_ready': 'Mark the pull request ready for review'}
    lines = ['Scope', plan.envelope.scope,
             *('- ' + item for item in plan.acceptance),
             'Allowed files: ' + ', '.join(plan.write_paths),
             'Excluded files: ' + ', '.join(plan.forbidden_paths)]
    if plan.non_goals:
        lines += ['Not included:', *('- ' + item for item in plan.non_goals)]
    lines += ['', 'Verification',
              *('- ' + check.obligation for check in plan.checks),
              '- Independent review of the changes and observed results.', '', 'Limits']
    lines += ['- ' + label for capability, label in grants.items() if capability in capabilities]
    if not capabilities:
        lines += ['- No execution capabilities granted.']
    if 'push' not in capabilities:
        lines += ['- No pushes.']
    if not {'draft_pr', 'mark_ready'} & capabilities:
        lines += ['- No pull-request publication or status changes.']
    lines += ['- No merging, deployment or credential changes.',
              f'- Budget: {plan.budget.seconds:g} seconds; {plan.budget.calls} tool calls.',
              '', 'Yes starts this exact plan; No cancels it; any other answer is taken as changes to the plan. '
              'Changed or expired plans cannot use this answer.',
              'Execution is trusted local and not sandboxed; scope checks detect changes, not prevent them.',
              '', 'Technical details',
              f'Repository: {plan.envelope.repo}; branch: {plan.envelope.branch}',
              f'Delivery: {plan.envelope.delivery_id}',
              f'Plan revision: {revision}',
              'The full frozen contract remains in the proposal; it is not repeated here.']
    return plan.goal, '\n'.join(lines)


CHOICES = ['Yes', 'No']


def question(plan, revision):
    goal, details = presentation(plan, revision)
    return 'Ready to start?\n\n' + goal + '\n\n' + details


def _valid(args, session_id, profile_id):
    return (type(args) is dict and set(args) == {'delivery_id', 'plan_revision'}
            and all(type(v) is str and v for v in args.values())
            and type(session_id) is str and session_id and type(profile_id) is str and profile_id)


def request(service, args, *, session_id, profile_id):
    """The question the user answers through Hermes clarify; the model never supplies the answer."""
    if not _valid(args, session_id, profile_id):
        return {'error': 'Use the exact delivery_id and plan_revision in their originating session; '
                         'consent cannot be supplied as an argument.'}
    try:
        previous = _previous(service.journal.db, _key(args), session_id, profile_id)
        if previous is not None:
            return previous
        state = _fresh(service, args, session_id, profile_id)
    except (ValueError, RuntimeError):
        return {'error': 'The proposal is unavailable, changed, expired or belongs to another session/profile.'}
    return {'delivery_id': args['delivery_id'], 'status': 'ask_user',
            'clarify': {'question': question(state.plan, args['plan_revision']), 'choices': CHOICES}}


def grant(service, state, key, session_id, profile_id):
    """Approve the frozen envelope for the user's own consent; False if the plan changed meanwhile."""
    try:
        event = Event(key, 'envelope_approved', state.plan.envelope.delivery_id,
            state.plan.digest(), state.artifact, state.version, int(time.time()),
            session_id=session_id, profile_id=profile_id, activity_id=state.active_intent)
        return service.accept(event)[0].approved
    except (ValueError, RuntimeError):
        return False


def answer(service, args, *, session_id, profile_id, asked, response):
    """Record a user's own clarify answer to the exact approval question; None if it is not one."""
    if not _valid(args, session_id, profile_id) or type(response) is not str:
        return None
    db = service.journal.db
    try:
        if _previous(db, _key(args), session_id, profile_id) is not None:
            return None
        state = _fresh(service, args, session_id, profile_id)
    except (ValueError, RuntimeError):
        return None
    if ' '.join(str(asked).split()) != ' '.join(question(state.plan, args['plan_revision']).split()):
        return None  # the user did not see this exact plan
    reply = response.strip().casefold()
    result = {'delivery_id': args['delivery_id'],
              'status': {'yes': 'approved', 'no': 'rejected'}.get(reply, 'changes_requested')}
    if reply == 'yes':
        result['status'] = 'approved' if grant(service, state, _key(args), session_id, profile_id) else 'stale_plan'
    record = {'schema': 1, 'kind': 'approval_answer', 'session_id': session_id,
              'profile_id': profile_id, 'result': result}
    # Archived immediately: an answer is never a launchable outbox intent. User text is not kept.
    db.execute('INSERT OR IGNORE INTO intent(id,payload,acknowledged) VALUES (?,?,1)', (_key(args), encoded(record)))
    return result
