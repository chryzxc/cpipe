"""Frozen v2 envelope consent via the existing Hermes one-operation transport.

The journal remains the approval authority. Archived outbox entries only prevent
re-prompting after dismissal or interruption; they never grant worker authority.
No database lock is held while waiting on a person. Execution is not sandboxed.
"""
import hashlib
import json
import time

from .artifact import validate_worktree
from .journal import encoded
from .service import Event


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
            or now >= state.plan.expires_at
            or now - state.started_at >= state.plan.budget.seconds
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


def _reserve(service, args, session_id, profile_id):
    db = service.journal.db
    db.execute('BEGIN IMMEDIATE')
    try:
        previous = _previous(db, _key(args), session_id, profile_id)
        if previous is not None:
            db.execute('COMMIT')
            return None, previous
        state = _fresh(service, args, session_id, profile_id)
        pending = {'delivery_id': args['delivery_id'], 'status': 'approval_pending'}
        record = {'schema': 1, 'kind': 'native_approval_attempt',
                  'session_id': session_id, 'profile_id': profile_id, 'result': pending}
        # Archived immediately: an approval attempt is never a launchable outbox intent.
        db.execute('INSERT INTO intent(id,payload,acknowledged) VALUES (?,?,1)',
                   (_key(args), encoded(record)))
        db.execute('COMMIT')
        return state, None
    except BaseException:
        db.execute('ROLLBACK')
        raise


def _finish(service, args, session_id, profile_id, result):
    record = {'schema': 1, 'kind': 'native_approval_attempt',
              'session_id': session_id, 'profile_id': profile_id, 'result': result}
    service.journal.db.execute('UPDATE intent SET payload=? WHERE id=?',
                              (encoded(record), _key(args)))
    return result


def presentation(plan, revision):
    """Readable copy from the frozen contract; no generated or inferred authority."""
    capabilities = plan.envelope.capabilities
    grants = {'local': 'Local development work', 'push': 'Push the branch',
              'draft_pr': 'Open a draft pull request',
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
              '', 'Approval applies only to this frozen plan. Approving does not start workers in the approval tool itself. '
              'In /cp-run, the coordinator continues '
              'execution after native approval; no second command is needed. Changed or expired plans cannot use this approval.',
              'Execution is trusted local and not sandboxed; scope checks detect changes, not prevent them.',
              '', 'Technical details',
              f'Repository: {plan.envelope.repo}; branch: {plan.envelope.branch}',
              f'Plan revision: {revision}',
              'The full frozen contract remains in the proposal; it is not repeated here.']
    return plan.goal, '\n'.join(lines)


def request(service, args, *, session_id, profile_id):
    """Only host transport consent can mint the reducer's envelope_approved event."""
    if (type(args) is not dict or set(args) != {'delivery_id', 'plan_revision'}
            or any(type(v) is not str or not v for v in args.values())
            or type(session_id) is not str or not session_id
            or type(profile_id) is not str or not profile_id):
        return {'error': 'Use the exact delivery_id and plan_revision in their originating session; '
                         'consent cannot be supplied as an argument.'}
    try:
        state, previous = _reserve(service, args, session_id, profile_id)
    except (ValueError, RuntimeError):
        return {'error': 'The v2 proposal is unavailable, changed, expired or belongs to another session/profile.'}
    if previous is not None:
        return previous
    from cpipe.plan_approval import _native_prompt
    command, description = presentation(state.plan, args['plan_revision'])
    try:
        decision = _native_prompt(command, description, 'cpipe_v2_envelope', 'Ready to start?')
    except Exception:
        decision = {}  # Never save transport diagnostics or credential-bearing exceptions.
    result = {'delivery_id': args['delivery_id'], 'status': 'waiting_for_approval'}
    valid = (type(decision) is dict and decision.get('resolved') is True
             and not any(decision.get(k) for k in ('cancelled', 'notify_failed', 'unavailable')))
    if valid and decision.get('choice') == 'deny':
        result['status'] = 'rejected'
    elif valid and decision.get('choice') == 'once':
        try:
            fresh = _fresh(service, args, session_id, profile_id)
            event = Event(_key(args), 'envelope_approved', args['delivery_id'],
                fresh.plan.digest(), fresh.artifact, fresh.version, int(time.time()),
                session_id=session_id, profile_id=profile_id, activity_id=fresh.active_intent)
            accepted, _ = service.accept(event)
            result['status'] = 'approved' if accepted.approved else 'stale_plan'
        except (ValueError, RuntimeError):
            result['status'] = 'stale_plan'
    return _finish(service, args, session_id, profile_id, result)
