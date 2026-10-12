from pathlib import Path
p = Path('cpipe/delivery/approach.py')
s = p.read_text()
old = '''        if record['state'] not in {'pending', 'draft'} or payload['expected_revision'] != record.get('draft_revision', 0):
            raise ValueError('draft is frozen or revision changed')
'''
new = '''        if record['state'] == 'proposal':  # the user answered the approval with changes: draft again
            state = controller.owned(service, record['delivery_id'], session_id, profile_id)
            if state.approved:
                raise ValueError('the plan is approved; build it or stop it')
            controller.cancel(service, record['delivery_id'], session_id=session_id, profile_id=profile_id)
            controller.discard(record['spec']['worktree'], state.plan.worktree)
            record = dict(record, state='draft')
            record.pop('delivery_id')
            _save(db, key, record)
        if record['state'] not in {'pending', 'draft'} or payload['expected_revision'] != record.get('draft_revision', 0):
            raise ValueError('draft is frozen or revision changed')
'''
assert old in s
s = s.replace(old, new)
p.write_text(s)
