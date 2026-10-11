"""Reviewed Markdown drafts for the single-command task flow.

Drafting never grants worker authority. A frozen plan includes the exact reviewed
text, and approval/build revalidate its private immutable-revision file. Herm's
preview and comments are chat context, not native execution consent.
"""
import hashlib
import json
import shlex
import os
from pathlib import Path
import stat

from . import controller
from .artifact import fingerprint
from .contracts import identity, string_tuple
from .journal import encoded, safe_file

MAX_DOCUMENT = 18000


def _safe(value):
    from agent.redact import redact_sensitive_text
    from ..runtime.envpolicy import Redactor
    material = encoded(value)
    if redact_sensitive_text(material) != material or Redactor(os.environ)(material) != material:
        raise ValueError('replace credentials with [REDACTED] before preparing an approach')


def _approach(value):
    fields = {'kind', 'depth', 'risk', 'summary', 'evidence', 'steps', 'decisions', 'risks'}
    if type(value) is not dict or set(value) != fields:
        raise ValueError('provide exact task approach fields')
    if value['kind'] not in {'bug', 'feature', 'change'} or value['depth'] not in {'focused', 'detailed'}:
        raise ValueError('unsupported task kind or approach depth')
    if value['risk'] not in {'normal', 'high'}:
        raise ValueError('unsupported task risk')
    identity(value['summary'], 'approach summary')
    for name in ('evidence', 'steps', 'decisions', 'risks'):
        if type(value[name]) is not list:
            raise ValueError('approach sections must be lists')
        string_tuple(tuple(value[name]), name)
    if not value['steps'] or (value['kind'] == 'bug' and not value['evidence']):
        raise ValueError('bugs require investigation evidence before a fix approach')
    if (value['kind'] == 'feature' or value['risk'] == 'high') and value['depth'] != 'detailed':
        raise ValueError('features and high-risk changes require detailed design')
    if value['depth'] == 'detailed' and not value['decisions']:
        raise ValueError('detailed design requires explicit decisions')


def render(spec, approach, revision):
    _approach(approach)
    lines = ['# ' + spec['goal'], '', f"Draft revision: {revision}",
             f"Task: {approach['kind']} · Approach: {approach['depth']} · Risk: {approach['risk']}",
             '', approach['summary'], '', '## Context',
             'Repository: ' + spec['repo'], 'Worktree: ' + spec['worktree'], 'Base: ' + spec['base']]
    def section(title, items):
        lines.extend(['', '## ' + title, *('- ' + item for item in items)])
    if approach['evidence']:
        section('Investigation' if approach['kind'] == 'bug' else 'Evidence and requirements', approach['evidence'])
    if approach['depth'] == 'detailed':
        section('Design decisions', approach['decisions'])
    section('Fix approach' if approach['kind'] == 'bug' else 'Implementation approach', approach['steps'])
    section('Acceptance', spec['acceptance'])
    section('Scope', ['Allowed files: ' + ', '.join(spec['write_paths']),
                      'Excluded files: ' + ', '.join(spec['forbidden_paths']), *spec['non_goals']])
    section('Verification', [c['obligation'] + ': `' + shlex.join(c['argv']) + '`' + (' → ' + ', '.join(c['criteria']) if c.get('criteria') else '')
                              for c in spec['checks']] + ['Independent review by the saved ' + spec['reviewer'] + ' profile.'])
    if approach['risks']:
        section('Risks and preserved behavior', approach['risks'])
    section('Execution limits', [f"Builder: {spec['builder']}; reviewer: {spec['reviewer']}. Saved models/providers are resolved by Hermes.",
        f"Execution budget: {spec['seconds']} seconds. Draft review time is not execution time.",
        'Local development only; no commits, pushes, PRs, merging, deployment or credential changes.',
        'Workers are trusted local processes, not sandboxed. Scope checks detect violations; they do not prevent access.'])
    section('Approval boundary', ['Use Open preview to review this document, then reply in the originating chat with any comments or changes.',
        'Comments revise the approach. Preview approval means the approach looks good; it does not grant execution authority.',
        'After review, cpipe freezes this exact approach and requests native execution approval.',
        'Only native approval of the current frozen plan can launch workers.'])
    text = '\n'.join(lines) + '\n'
    if len(text.encode()) > MAX_DOCUMENT:
        raise ValueError('approach document is too large')
    return text


def _directory(home):
    path = Path(home).absolute() / 'cpipe-deliveries' / 'approaches'
    if path.resolve() != path:
        raise ValueError('symlink approach directory')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('unsafe approach directory')
    return path


def _document(path):
    path = Path(path)
    if path.resolve(strict=True) != path:
        raise ValueError('symlink approach document')
    parent = path.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700):
        raise ValueError('unsafe approach directory')
    safe_file(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600:
            raise ValueError('unsafe approach file')
        content = stream.read(MAX_DOCUMENT + 1)
        after = os.fstat(stream.fileno())
    current = safe_file(path)
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if signature(before) != signature(after) or signature(after) != signature(current) or len(content) > MAX_DOCUMENT:
        raise ValueError('approach changed during read')
    return content.decode('utf-8')


def validate_document(service, plan):
    if not plan.approach:
        return
    row = service.journal.db.execute('SELECT payload FROM intent WHERE id=?',
                                    ('approach:' + plan.digest(),)).fetchone()
    if not row or _document(json.loads(row[0])['document_path']) != plan.approach:
        raise ValueError('reviewed approach document changed or is unavailable')


def roles(home):
    """(saved builder/reviewer or None, {profile: model} of profiles that can run as workers)."""
    from hermes_cli.profiles import list_profile_names
    from .live_probe import observe_actor
    usable = {}
    for name in list_profile_names():
        try:
            usable[name] = observe_actor('builder', name).model
        except ValueError:
            pass  # no saved model/provider: cannot run unattended
    try:
        saved = json.loads((Path(home) / 'cpipe-deliveries' / 'roles.json').read_text())
        saved = {'builder': saved['builder'], 'reviewer': saved['reviewer']}
    except (OSError, ValueError, KeyError, TypeError):
        saved = None
    if saved and (saved['builder'] == saved['reviewer'] or not set(saved.values()) <= set(usable)):
        saved = None  # a remembered profile was removed or lost its model
    return saved, usable


def remember_roles(home, spec):
    path = Path(home) / 'cpipe-deliveries' / 'roles.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'builder': spec['builder'], 'reviewer': spec['reviewer']}) + '\n')


def _save(db, key, record):
    db.execute('UPDATE intent SET payload=? WHERE id=?', (encoded(record), key))


def execute(service, record, key, op, payload, *, home, session_id, profile_id, launch):
    """Run-only stage machine; caller has checked token/session/profile/expiry."""
    db = service.journal.db
    _safe(payload)
    if op == 'status':
        if payload:
            raise ValueError('run status takes no arguments')
        if record['state'] == 'claimed' and not record.get('delivery_id'):
            return {'phase': 'claimed', 'operation': record['claimed_operation'], 'approved': False,
                    'next_step': 'A stage is in progress or interrupted; do not infer completion or automatically relaunch.'}
        if record.get('delivery_id'):
            return controller.summary(controller.owned(service, record['delivery_id'], session_id, profile_id), service)
        return record.get('result') or {'phase': record['state'], 'approved': False}
    if op == 'prepare':
        if set(payload) != {'spec', 'approach', 'expected_revision'} or type(payload['expected_revision']) is not int:
            raise ValueError('prepare requires spec, approach and exact expected_revision')
        request_digest = hashlib.sha256(encoded(payload).encode()).hexdigest()
        if record['state'] == 'draft' and record.get('request_digest') == request_digest:
            return record['result']
        if record['state'] not in {'pending', 'draft'} or payload['expected_revision'] != record.get('draft_revision', 0):
            raise ValueError('draft is frozen or revision changed')
        spec, approach = payload['spec'], payload['approach']
        artifact, _, _ = controller.validate_spec(spec)
        revision = record.get('draft_revision', 0) + 1
        text = render(spec, approach, revision)
        # The directory is outside the worktree: Markdown cannot invalidate source identity.
        root = _directory(home)
        if root.is_relative_to(Path(spec['worktree'])):
            raise ValueError('approach must be outside source')
        path = root / (key.split(':')[-1] + f'-r{revision}-{hashlib.sha256(text.encode()).hexdigest()[:16]}.md')
        db.execute('BEGIN IMMEDIATE')
        try:
            current = json.loads(db.execute('SELECT payload FROM intent WHERE id=?', (key,)).fetchone()[0])
            if current != record:
                raise ValueError('run receipt changed during preparation')
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                if _document(path) != text:
                    raise ValueError('conflicting approach revision')
            else:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(text)
            # Herm's MEDIA parser accepts literal quote delimiters, not JSON escapes.
            delimiter = next((q for q in ('"', "'", '`') if q not in str(path)), None)
            if delimiter is None or any(ord(c) < 32 for c in str(path)):
                raise ValueError('approach path cannot be safely presented as an attachment')
            result = dict(phase='approach_review', approved=False, draft_revision=revision,
                          document_path=str(path), preview_link=f'[Open preview]({path.as_uri()})',
                          preview_attachment='MEDIA:' + delimiter + str(path) + delimiter,
                          next_step='Present preview_attachment on its own line in the assistant reply for the clickable Open preview card. '
                          'Plain file links may be disabled. Wait for the user\'s comments or proceed. '
                          'Revise using prepare with expected_revision; after the user is ready, plan with draft_revision. '
                          'Preview approval is not execution consent.')
            record.update(state='draft', spec=spec, approach=approach, text=text,
                          artifact=artifact.digest, draft_revision=revision, request_digest=request_digest, result=result)
            _save(db, key, record)
            db.execute('COMMIT')
            return result
        except BaseException:
            db.execute('ROLLBACK')
            raise
    if op == 'plan':
        if set(payload) != {'draft_revision'} or type(payload['draft_revision']) is not int or payload['draft_revision'] != record.get('draft_revision'):
            raise ValueError('freeze only the exact reviewed draft revision')
        if record['state'] == 'proposal':
            return record['result']
        if record['state'] != 'draft':
            raise ValueError('run requires an approach draft before freezing')
        if _document(record['result']['document_path']) != record['text']:
            raise ValueError('approach changed; revise it before approval')
        if fingerprint(record['spec']['worktree']).digest != record['artifact']:
            raise ValueError('source changed since investigation; revise the approach')
    elif op == 'build':
        if payload != {'delivery_id': record.get('delivery_id')} or record['state'] not in {'proposal', 'complete'}:
            raise ValueError('build only the delivery frozen by this run receipt')
        state = controller.owned(service, record['delivery_id'], session_id, profile_id)
        if not state.approved:
            raise ValueError('native frozen-plan approval is required before execution')
        validate_document(service, state.plan)
        if record['state'] == 'complete':
            return record['result']
    else:
        raise ValueError('unsupported run stage')
    # Reserve exactly once. No database lock is held during the controller/worker.
    db.execute('BEGIN IMMEDIATE')
    try:
        current = json.loads(db.execute('SELECT payload FROM intent WHERE id=?', (key,)).fetchone()[0])
        if current != record:
            raise ValueError('run receipt changed before launch')
        record.update(state='claimed', claimed_operation=op)
        _save(db, key, record)
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise
    if op == 'plan':
        state = controller.from_spec(service, record['spec'], session_id=session_id, profile_id=profile_id,
                                     approach=record['text'])
        remember_roles(home, record['spec'])  # the next /cp-run reuses these without asking
        binding = {'document_path': record['result']['document_path']}
        db.execute('INSERT INTO intent(id,payload,acknowledged) VALUES (?,?,1)',
                   ('approach:' + state.plan.digest(), encoded(binding)))
        result = controller.summary(state)
        result.update(document_path=binding['document_path'], preview_link=record['result']['preview_link'],
                      preview_attachment=record['result']['preview_attachment'],
                      draft_revision=record['draft_revision'])
        record.update(state='proposal', delivery_id=result['delivery_id'], result=result)
    else:
        result = launch(record['delivery_id'])
        record.update(state='complete', result=result)
    _save(db, key, record)
    return result
