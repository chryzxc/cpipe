from pathlib import Path
p = Path('cpipe/delivery/approval.py')
s = p.read_text()
start = s.index('def _key(args):')
end = s.index('def presentation(plan, revision):')
s = s[:start] + '''def _key(args):
    return 'approval-ui:' + hashlib.sha256(encoded(args).encode()).hexdigest()


def _previous(db, key, session_id, profile_id):
    row = db.execute('SELECT payload FROM intent WHERE id=?', (key,)).fetchone()
    if row is None:
        return None
    record = json.loads(row[0])
    if record['session_id'] != session_id or record['profile_id'] != profile_id:
        raise ValueError('approval belongs to another session or profile')
    return record['result']


''' + s[end:]
s = s[:s.index('def request(service, args')]
s = s.replace('''              '', 'Approval applies only to this frozen plan. Approving does not start workers in the approval tool itself. '
              'In /cp-run, the coordinator continues '
              'execution after native approval; no second command is needed. Changed or expired plans cannot use this approval.',
              'Execution is trusted local and not sandboxed; scope checks detect changes, not prevent them.',''',
'''              '', 'Yes starts this exact plan; No cancels it; any other answer is taken as changes to the plan. '
              'Changed or expired plans cannot use this answer.',
              'Execution is trusted local and not sandboxed; scope checks detect changes, not prevent them.',''')
s = s.replace("""              f'Plan revision: {revision}',""", """              f'Delivery: {plan.envelope.delivery_id}',
              f'Plan revision: {revision}',""")
s += '''CHOICES = ['Yes', 'No']


def question(plan, revision):
    goal, details = presentation(plan, revision)
    return 'Ready to start?\\n\\n' + goal + '\\n\\n' + details


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
        try:
            event = Event(_key(args), 'envelope_approved', args['delivery_id'],
                state.plan.digest(), state.artifact, state.version, int(time.time()),
                session_id=session_id, profile_id=profile_id, activity_id=state.active_intent)
            accepted, _ = service.accept(event)
            result['status'] = 'approved' if accepted.approved else 'stale_plan'
        except (ValueError, RuntimeError):
            result['status'] = 'stale_plan'
    record = {'schema': 1, 'kind': 'approval_answer', 'session_id': session_id,
              'profile_id': profile_id, 'result': result}
    # Archived immediately: an answer is never a launchable outbox intent. User text is not kept.
    db.execute('INSERT OR IGNORE INTO intent(id,payload,acknowledged) VALUES (?,?,1)', (_key(args), encoded(record)))
    return result
'''
s = s.replace('''"""Frozen envelope consent via the existing Hermes one-operation transport.

The journal remains the approval authority. Archived outbox entries only prevent
re-prompting after dismissal or interruption; they never grant worker authority.
No database lock is held while waiting on a person. Execution is not sandboxed.
"""''', '''"""Frozen envelope consent through Hermes clarify.

The model asks the exact question; only the user's answer, observed from the host's
clarify result, can approve. The journal remains the approval authority. Execution is
not sandboxed.
"""''')
p.write_text(s)
