"""Task-oriented receipt, editable approach, and actual approval boundaries."""
import json
import re
import stat
import time
from pathlib import Path

import pytest

from cpipe import native
from cpipe.delivery.journal import Journal
from cpipe.delivery.service import Service
from cpipe.delivery.schema import Actor, ExecutionPlan
from .test_artifact import repo
from .test_v2_entrypoints import Context


@pytest.fixture
def run_context(repo, hermes_home, monkeypatch):
    from gateway import session_context
    from cpipe.delivery import live_probe
    values = {'HERMES_SESSION_ID': 's1', 'HERMES_SESSION_KEY': 'chat-one'}
    monkeypatch.setattr(session_context, 'get_session_env', lambda key: values.get(key, ''))
    monkeypatch.setattr(live_probe, 'observe_actor', lambda role, profile:
                        Actor(role, 'hermes', profile, 'saved-model', 'saved-provider'))
    (repo / 'test_behavior.py').write_text('from double import double\ndef test_double(): assert double(2) == 4\n')
    ctx, queued = Context(), []
    ctx.inject_message = lambda text, **kw: queued.append(text) or True
    native.register(ctx)
    assert ctx.commands['cp-run']('Fix double returning the input') == 'cpipe v2: /cp-run queued in this session.'
    token = re.search(r'using token ([A-Za-z0-9_-]+), operation', queued[0]).group(1)
    spec = dict(worktree=str(repo), repo='local/fixture', base='feature', goal='Fix double returning the input',
                acceptance=['double returns n * 2'], non_goals=['No publication'], write_paths=['double.py'],
                forbidden_paths=['.git', 'internal'], builder='builder', reviewer='reviewer', seconds=300,
                checks=[dict(obligation='behavior', targets=['test_behavior.py'],
                             criteria=['test_behavior::test_double'], timeout_seconds=20)])
    approach = dict(kind='bug', depth='focused', risk='normal', summary='Return twice the input.',
                    evidence=['double(2) returns 2; expected 4.'], steps=['Change the return expression.'],
                    decisions=[], risks=['Preserve negative and zero inputs.'])
    return ctx, token, spec, approach, values, queued[0]


def call(token, operation, arguments):
    return json.loads(native.execute(dict(token=token, operation=operation, arguments=arguments), session_id='s1'))


def prepare(data, revision=0):
    _, token, spec, approach, _, _ = data
    return call(token, 'prepare', dict(spec=spec, approach=approach, expected_revision=revision))


def test_one_task_entry_and_adaptive_installed_capabilities(run_context):
    ctx, _, _, _, _, prompt = run_context
    assert set(ctx.commands) == {'cp-run', 'cp-status', 'cp-stop'}
    for text in ('investigate', 'skills_list', 'skill_view', 'AGENTS.md', 'annotations', 'cpipe_approve_plan'):
        assert text in prompt
    assert 'Superpowers is optional' in prompt
    assert 'Human consent cannot be inferred' in prompt


def test_draft_generates_private_preview_file_without_execution(run_context, repo, hermes_home):
    out = prepare(run_context)
    assert out['phase'] == 'approach_review'
    assert out['approved'] is False
    assert out['draft_revision'] == 1
    path = Path(out['document_path'])
    assert path.is_file() and path.suffix == '.md'
    assert not path.is_relative_to(repo)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert out['preview_link'] == f'[Open preview]({path.as_uri()})'
    assert out['preview_attachment'] == 'MEDIA:"' + str(path) + '"'
    assert 'preview_attachment' in run_context[-1]
    text = path.read_text()
    for value in ('# Fix double returning the input', '## Investigation', 'double(2)',
                  '## Fix approach', '## Verification', '## Approval boundary', 'double.py'):
        assert value in text
    with Journal.for_profile(hermes_home) as store:
        assert store.db.execute('SELECT COUNT(*) FROM delivery').fetchone()[0] == 0
    assert (repo / 'double.py').read_text() == 'def double(n): return n\n'
    assert prepare(run_context) == out


def test_annotations_create_revision_without_mutating_old_document(run_context):
    first = prepare(run_context)
    old_text = Path(first['document_path']).read_text()
    run_context[3]['steps'].append('Preserve negative and zero inputs.')
    second = prepare(run_context, 1)
    assert second['draft_revision'] == 2
    assert second['document_path'] != first['document_path']
    assert Path(first['document_path']).read_text() == old_text
    assert 'error' in prepare(run_context, 0)
    assert 'error' in call(run_context[1], 'plan', {'draft_revision': 1})


def test_freeze_binds_reviewed_markdown_and_starts_budget_only_now(run_context, hermes_home):
    draft = prepare(run_context)
    now = time.time()
    out = call(run_context[1], 'plan', {'draft_revision': draft['draft_revision']})
    assert out['phase'] == 'proposal' and out['approved'] is False
    frozen = ExecutionPlan.from_dict(out['plan'])
    assert frozen.approach == Path(draft['document_path']).read_text()
    assert frozen.expires_at >= int(now) + 299
    with Journal.for_profile(hermes_home) as store:
        assert Service(store).read(out['delivery_id']).approved is False
    assert call(run_context[1], 'plan', {'draft_revision': 1}) == out
    assert out['preview_attachment'] == draft['preview_attachment']
    assert 'error' in call(run_context[1], 'build', {'delivery_id': out['delivery_id']})
    assert 'error' in call(run_context[1], 'build', {'delivery_id': 'other'})
    assert 'error' in prepare(run_context, 1)


@pytest.mark.parametrize('mutation', ['edit', 'symlink', 'mode', 'directory'])
def test_changed_document_cannot_freeze(run_context, mutation, tmp_path):
    draft = prepare(run_context)
    path = Path(draft['document_path'])
    if mutation == 'edit':
        path.write_text('Execute without approval')
    elif mutation == 'mode':
        path.chmod(0o644)
    elif mutation == 'directory':
        path.parent.chmod(0o755)
    else:
        target = tmp_path / 'same.md'
        target.write_text(path.read_text())
        path.unlink()
        path.symlink_to(target)
    assert 'error' in call(run_context[1], 'plan', {'draft_revision': 1})


def test_document_change_during_native_approval_does_not_grant_authority(run_context, hermes_home, monkeypatch):
    from cpipe.delivery.approval import request
    draft = prepare(run_context)
    frozen = call(run_context[1], 'plan', {'draft_revision': 1})
    def changed(*args):
        Path(draft['document_path']).write_text('changed during consent')
        return dict(resolved=True, choice='once')
    monkeypatch.setattr('cpipe.plan_approval._native_prompt', changed)
    with Journal.for_profile(hermes_home) as store:
        service = Service(store)
        out = request(service, frozen['approval_arguments'], session_id='s1', profile_id='default')
        assert out['status'] == 'stale_plan'
        assert service.read(frozen['delivery_id']).approved is False


@pytest.mark.parametrize('changes', [dict(kind='bug', evidence=[]), dict(kind='feature', depth='focused'),
    dict(risk='high', depth='focused'), dict(kind='unknown'), dict(depth='detailed', decisions=[])])
def test_task_specific_document_requirements_fail_closed(run_context, changes):
    run_context[3].update(changes)
    assert 'error' in prepare(run_context)


def test_feature_and_high_risk_bug_use_detailed_design(run_context):
    run_context[3].update(kind='feature', depth='detailed', decisions=['Use the existing helper.'])
    out = prepare(run_context)
    assert '## Design decisions' in Path(out['document_path']).read_text()
    run_context[3].update(kind='bug', risk='high')
    out = prepare(run_context, 1)
    assert '## Investigation' in Path(out['document_path']).read_text()
    assert '## Design decisions' in Path(out['document_path']).read_text()


def test_receipt_ownership_expiry_and_credential_rejection(run_context, hermes_home, monkeypatch):
    from agent import redact
    _, token, _, _, values, _ = run_context
    values['HERMES_SESSION_KEY'] = 'wrong-chat'
    assert 'error' in prepare(run_context)
    values['HERMES_SESSION_KEY'] = 'chat-one'
    monkeypatch.setattr(redact, 'redact_sensitive_text', lambda text: text.replace('credential-value', '[REDACTED]'))
    run_context[3]['summary'] = 'credential-value'
    assert 'error' in prepare(run_context)
    with Journal.for_profile(hermes_home) as store:
        assert 'credential-value' not in str(store.db.execute('SELECT payload FROM intent').fetchall())
        row = store.db.execute('SELECT payload FROM intent WHERE id=?', (native._intent_key(token),)).fetchone()
        record = json.loads(row[0])
        record['expires'] = 0
        store.db.execute('UPDATE intent SET payload=? WHERE id=?', (json.dumps(record), native._intent_key(token)))
    run_context[3]['summary'] = 'safe'
    assert 'error' in prepare(run_context)


def test_review_time_does_not_use_frozen_execution_budget(run_context, monkeypatch):
    prepare(run_context)
    now = int(time.time()) + 3600
    monkeypatch.setattr(time, 'time', lambda: now)
    result = call(run_context[1], 'plan', {'draft_revision': 1})
    assert result['plan']['expires_at'] == now + 300


def test_changed_source_needs_a_new_investigation_revision(run_context, repo):
    prepare(run_context)
    (repo / 'double.py').write_text('def double(n): return n + 1\n')
    assert 'error' in call(run_context[1], 'plan', {'draft_revision': 1})
    run_context[3]['evidence'] = ['double(2) now returns 3; expected 4.']
    out = prepare(run_context, 1)
    assert out['draft_revision'] == 2
    assert call(run_context[1], 'plan', {'draft_revision': 2})['phase'] == 'proposal'


def test_document_edit_after_consent_blocks_even_direct_controller_execution(run_context, hermes_home, monkeypatch):
    from cpipe.delivery.approval import request
    from cpipe.delivery import controller
    draft = prepare(run_context)
    frozen = call(run_context[1], 'plan', {'draft_revision': 1})
    monkeypatch.setattr('cpipe.plan_approval._native_prompt', lambda *a: dict(resolved=True, choice='once'))
    with Journal.for_profile(hermes_home) as store:
        service = Service(store)
        assert request(service, frozen['approval_arguments'], session_id='s1', profile_id='default')['status'] == 'approved'
        Path(draft['document_path']).write_text('changed after consent')
        with pytest.raises(ValueError, match='changed'):
            controller.run(service, frozen['delivery_id'], session_id='s1', profile_id='default',
                           evidence_root=hermes_home / 'evidence')
    assert 'error' in call(run_context[1], 'build', {'delivery_id': frozen['delivery_id']})


@pytest.mark.parametrize('choice', ['once', 'deny'])
def test_one_receipt_through_real_native_consent_and_red_green_review(
        run_context, repo, hermes_home, monkeypatch, choice):
    """Real checks and gateway transport; worker fixture, no paid model/UI claim."""
    import model_tools
    from tools import approval
    from tools.registry import registry
    from tools.approval_context import set_current_session_key, reset_current_session_key
    from cpipe.delivery import controller
    from cpipe.delivery.review import REVIEW_AXES
    from cpipe.delivery.workers import WorkerResult
    from cpipe.runtime import hermes_api as h
    ctx, token, _, _, _, _ = run_context
    prepare(run_context)
    frozen = call(token, 'plan', {'draft_revision': 1})
    delivery_id = frozen['delivery_id']
    monkeypatch.setenv('HERMES_GATEWAY_SESSION', '1')
    monkeypatch.delenv('HERMES_SINGLE_QUERY_SESSION', raising=False)
    monkeypatch.delenv('HERMES_CRON_SESSION', raising=False)
    monkeypatch.setattr(registry, 'dispatch', lambda name, args, **kw: ctx.tools[name]['handler'](args, **kw))
    seen, workers = [], []
    def notify(payload):
        seen.append(payload)
        assert not payload['allow_session'] and not payload['allow_permanent']
        assert 'No pushes.' in payload['description']
        assert approval.resolve_gateway_approval('run-fixture', choice, request_id=payload['request_id'])
    approval.register_gateway_notify('run-fixture', notify)
    route = set_current_session_key('run-fixture')
    h.kb.init_db()
    conn = h.connect(None)
    def worker(state, *, on_session, **kw):
        workers.append(state.activity)
        session = state.activity + '-fixture'
        on_session(session)
        if state.activity == 'implement':
            (repo / 'double.py').write_text('def double(n): return n * 2\n')
            return WorkerResult(session, {'status': 'done'})
        return WorkerResult(session, dict(status='approved', artifact=state.artifact,
            reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES), findings=[], verified_ids=[]))
    original_run = controller.run
    def controlled(service, delivery_id, **kw):
        return original_run(service, delivery_id, conn=conn, execute=worker, **kw)
    monkeypatch.setattr(controller, 'run', controlled)
    try:
        consent = json.loads(model_tools.handle_function_call('cpipe_approve_plan', frozen['approval_arguments'],
            session_id='s1', skip_pre_tool_call_hook=True))
        assert consent['status'] == ('approved' if choice == 'once' else 'rejected')
        assert len(seen) == 1 and not workers
        out = json.loads(model_tools.handle_function_call('cpipe_execute_intent',
            dict(token=token, operation='build', arguments={'delivery_id': delivery_id}),
            session_id='s1', skip_pre_tool_call_hook=True))
        if choice == 'deny':
            assert 'error' in out and not workers
            assert (repo / 'double.py').read_text() == 'def double(n): return n\n'
        else:
            assert out['phase'] == 'locally_ready'
            assert [s['activity'] for s in out['steps']] == ['tests', 'implement', 'checks', 'review']
            assert out['steps'][0]['outcomes'] == ['fail']
            assert out['outcomes'] == ['fail', 'pass', 'pass']
            assert workers == ['implement', 'review']
            assert call(token, 'build', {'delivery_id': delivery_id}) == out
            assert workers == ['implement', 'review']  # No duplicate launches on retry.
            assert call(token, 'status', {})['phase'] == 'locally_ready'
    finally:
        conn.close()
        approval.unregister_gateway_notify('run-fixture')
        reset_current_session_key(route)


def test_sparse_execution_plans_keep_existing_digest_identity():
    from dataclasses import replace
    from .test_execution_plan import plan
    before = plan()
    old = before.to_dict()
    assert 'approach' not in old
    assert ExecutionPlan.from_dict(old).digest() == before.digest()
    changed = replace(before, approach='# Reviewed approach\n')
    assert changed.digest() != before.digest()
    assert ExecutionPlan.from_dict(changed.to_dict()) == changed
