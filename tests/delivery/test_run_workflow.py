"""Task-oriented receipt, editable approach, and actual approval boundaries."""
import json
from contextlib import closing
import sys
import re
import stat
import subprocess
import time
from pathlib import Path

import pytest

from cpipe import native
from cpipe.delivery.journal import Journal
from cpipe.delivery.service import Service
from cpipe.delivery.schema import Actor, ExecutionPlan
from .test_artifact import repo
from .test_entrypoints import Context


@pytest.fixture
def run_context(repo, hermes_home, monkeypatch):
    from gateway import session_context
    from cpipe.delivery import live_probe
    values = {'HERMES_SESSION_ID': 's1', 'HERMES_SESSION_KEY': 'chat-one'}
    monkeypatch.setattr(session_context, 'get_session_env', lambda key: values.get(key, ''))
    monkeypatch.setattr(live_probe, 'observe_actor', lambda role, profile:
                        Actor(role, 'hermes', profile, 'saved-model', 'saved-provider'))
    from cpipe.delivery import approach
    monkeypatch.setattr(approach, 'roles', lambda home: ({'builder': 'builder', 'reviewer': 'reviewer'},
                                                         {'builder': 'saved-model', 'reviewer': 'saved-model'}))
    (repo / 'test_behavior.py').write_text('from double import double\ndef test_double(): assert double(2) == 4\n')
    ctx, queued = Context(), []
    ctx.inject_message = lambda text, **kw: queued.append(text) or True
    native.register(ctx)
    assert ctx.commands['cp-run']('Fix double returning the input') == 'cpipe: /cp-run queued in this session.'
    token = re.search(r'using token ([A-Za-z0-9_-]+), operation', queued[0]).group(1)
    spec = dict(worktree=str(repo), repo='local/fixture', base='feature', goal='Fix double returning the input',
                acceptance=['double returns n * 2'], non_goals=['No publication'], write_paths=['double.py'],
                forbidden_paths=['.git', 'internal'], builder='builder', reviewer='reviewer', seconds=300,
                checks=[dict(obligation='behavior', argv=[sys.executable, '-m', 'pytest', 'test_behavior.py', '-q',
                             '-p', 'no:cacheprovider', '--junitxml={report}'],
                             criteria=['test_behavior::test_double'], timeout_seconds=20)])
    approach = dict(kind='bug', depth='focused', risk='normal', summary='Return twice the input.',
                    evidence=['double(2) returns 2; expected 4.'], steps=['Change the return expression.'],
                    decisions=[], risks=['Preserve negative and zero inputs.'])
    return ctx, token, spec, approach, values, queued[0]


def frozen_plan(delivery_id):
    with native.scope() as (service, _, _home):
        return service.read(delivery_id).plan


def call(token, operation, arguments):
    return json.loads(native.execute(dict(token=token, operation=operation, arguments=arguments), session_id='s1'))


def reply(arguments, response, asked=None):
    """The user answers cpipe's approval question in Hermes clarify; cpipe sees the host's result."""
    from cpipe import plan_approval
    from .test_native_approval import clarify
    asked = asked or plan_approval.request(arguments, 's1')['clarify']['question']
    questions = [{'question': asked, 'choices': ['Yes', 'No']}]
    plan_approval.observe(tool_name='clarify', args={'questions': questions}, session_id='s1',
                          result=clarify(questions, {'q0': response}))
    return plan_approval.request(arguments, 's1')


def prepare(data, revision=0):
    _, token, spec, approach, _, _ = data
    return call(token, 'prepare', dict(spec=spec, approach=approach, expected_revision=revision))


def test_one_task_entry_and_adaptive_installed_capabilities(run_context):
    ctx, _, _, _, _, prompt = run_context
    assert set(ctx.commands) == {'cp-run', 'cp-spawn', 'cp-status', 'cp-stop'}
    for text in ('investigate', 'AGENTS.md', 'reply with any comments or changes', 'cpipe_approve_plan'):
        assert text in prompt
    assert 'skills_list' not in prompt
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


def test_comments_create_revision_without_mutating_old_document(run_context):
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
    frozen = frozen_plan(out['delivery_id'])
    assert frozen.approach == Path(draft['document_path']).read_text()
    assert frozen.expires_at >= int(now) + 299
    with Journal.for_profile(hermes_home) as store:
        assert Service(store).read(out['delivery_id']).approved is False
    assert call(run_context[1], 'plan', {'draft_revision': 1}) == out
    assert out['preview_attachment'] == draft['preview_attachment']
    assert 'error' in call(run_context[1], 'build', {'delivery_id': out['delivery_id']})
    assert 'error' in call(run_context[1], 'build', {'delivery_id': 'other'})
    assert 'error' in prepare(run_context, 0)  # a stale revision never reopens the frozen plan


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


def test_document_change_before_the_answer_does_not_grant_authority(run_context, hermes_home):
    from cpipe import plan_approval
    draft = prepare(run_context)
    frozen = call(run_context[1], 'plan', {'draft_revision': 1})
    asked = plan_approval.request(frozen['approval_arguments'], 's1')['clarify']['question']
    Path(draft['document_path']).write_text('changed during consent')
    assert 'error' in reply(frozen['approval_arguments'], 'Yes', asked)
    with Journal.for_profile(hermes_home) as store:
        assert Service(store).read(frozen['delivery_id']).approved is False


def test_changes_in_the_answer_reopen_the_draft_and_the_new_plan_is_asked_again(run_context, repo, hermes_home):
    token = run_context[1]
    prepare(run_context)
    first = call(token, 'plan', {'draft_revision': 1})
    assert reply(first['approval_arguments'], 'Also handle floats')['status'] == 'changes_requested'
    assert 'error' in call(token, 'build', {'delivery_id': first['delivery_id']})
    assert prepare(run_context, revision=1)['draft_revision'] == 2
    with Journal.for_profile(hermes_home) as store:
        assert Service(store).read(first['delivery_id']).phase == 'cancelled'
    assert first['delivery_id'] not in subprocess.run(['git', 'worktree', 'list'], cwd=repo, capture_output=True,
                                                      text=True).stdout
    second = call(token, 'plan', {'draft_revision': 2})
    assert second['delivery_id'] != first['delivery_id']
    assert reply(second['approval_arguments'], 'Yes')['status'] == 'approved'
    assert 'error' in prepare(run_context, revision=2)  # an approved plan is built or stopped, not redrafted


def test_one_run_carries_several_independent_tasks(run_context, hermes_home):
    _, token, spec, approach_, _, _ = run_context
    first = call(token, 'prepare', dict(spec=spec, approach=approach_, expected_revision=0, task='t2'))
    other = dict(spec, goal='Document double', acceptance=['README explains double'])
    second = call(token, 'prepare', dict(spec=other, approach=approach_, expected_revision=0, task='t3'))
    assert first['document_path'] != second['document_path']
    a = call(token, 'plan', {'draft_revision': 1, 'task': 't2'})
    b = call(token, 'plan', {'draft_revision': 1, 'task': 't3'})
    assert a['delivery_id'] != b['delivery_id'] and frozen_plan(a['delivery_id']).worktree != frozen_plan(b['delivery_id']).worktree
    assert 'error' in call(token, 'plan', {'draft_revision': 1, 'task': 'T 4'})
    assert 'error' in call(token, 'build', {'delivery_id': a['delivery_id'], 'task': 't9'})  # unknown task
    assert 'error' in call(token, 'build', {'delivery_id': a['delivery_id'], 'task': 't3'})  # another task's delivery
    assert reply(a['approval_arguments'], 'Yes')['status'] == 'approved'
    assert reply(b['approval_arguments'], 'No')['status'] == 'rejected'
    assert 'error' in call(token, 'build', {'delivery_id': b['delivery_id'], 'task': 't3'})


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
    from cpipe.delivery.controller import APPROVAL_WINDOW
    assert frozen_plan(result['delivery_id']).expires_at == now + APPROVAL_WINDOW + 300


def test_a_delivery_starts_from_what_was_investigated_in_a_worktree_of_its_own(run_context, repo, hermes_home):
    from cpipe.delivery.service import Service
    import subprocess
    prepare(run_context)  # the untracked behavior test is carried over from the checkout
    (repo / 'double.py').write_text('def double(n): return n + 1\n')  # later edits in the checkout do not matter
    git = lambda *a: subprocess.run(['git', *a], cwd=repo, check=True, capture_output=True, text=True).stdout
    git('worktree', 'add', '-q', '.worktrees/other', '-b', 'other')  # nor do other worktrees inside it
    frozen = call(run_context[1], 'plan', {'draft_revision': 1})
    assert frozen['phase'] == 'proposal'
    with Journal.for_profile(hermes_home) as j:
        plan = Service(j).read(frozen['delivery_id']).plan
    root = Path(plan.worktree)
    assert root.parent == repo.parent / (repo.name + '-worktrees') and (root / 'test_behavior.py').exists()
    assert (root / 'double.py').read_text() == 'def double(n): return n\n'
    assert not (root / '.worktrees').exists()
    assert git('status', '--porcelain', '--', 'double.py').strip() == 'M double.py'  # the checkout keeps its edit
    assert 'cpipe/' + frozen['delivery_id'] in git('branch', '--list', 'cpipe/*')


def test_document_edit_after_consent_blocks_even_direct_controller_execution(run_context, hermes_home):
    from cpipe.delivery import controller
    draft = prepare(run_context)
    frozen = call(run_context[1], 'plan', {'draft_revision': 1})
    assert reply(frozen['approval_arguments'], 'Yes')['status'] == 'approved'
    with Journal.for_profile(hermes_home) as store:
        service = Service(store)
        Path(draft['document_path']).write_text('changed after consent')
        with pytest.raises(ValueError, match='changed'):
            controller.run(service, frozen['delivery_id'], session_id='s1', profile_id='default',
                           evidence_root=hermes_home / 'evidence')
    assert 'error' in call(run_context[1], 'build', {'delivery_id': frozen['delivery_id']})


@pytest.mark.parametrize('choice', ['Yes', 'No'])
def test_one_receipt_through_the_users_answer_and_red_green_review(
        run_context, repo, hermes_home, monkeypatch, choice):
    """Real checks and clarify result; worker fixture, no paid model/UI claim."""
    import model_tools
    from tools.registry import registry
    from cpipe.delivery import controller
    from cpipe.delivery.review import REVIEW_AXES
    from cpipe.delivery.workers import WorkerResult
    from cpipe.runtime import hermes_api as h
    ctx, token, _, _, _, _ = run_context
    # Already failing before the change (another worktree's lint): never blocks the run.
    run_context[2]['checks'].append(dict(obligation='lint', argv=['sh', '-c', 'echo "other/x.js 1:1 error"; exit 1'],
                                         timeout_seconds=20))
    prepare(run_context)
    frozen = call(token, 'plan', {'draft_revision': 1})
    delivery_id = frozen['delivery_id']
    monkeypatch.setattr(registry, 'dispatch', lambda name, args, **kw: ctx.tools[name]['handler'](args, **kw))
    workers = []
    h.kb.init_db()
    told = []
    ctx.inject_message = lambda text, **kw: told.append((text, kw)) or True
    def worker(state, *, on_session, **kw):
        workers.append(state.activity)
        session = state.activity + '-fixture'
        on_session(session)
        if state.activity == 'implement':
            (Path(state.plan.worktree) / 'double.py').write_text('def double(n): return n * 2\n')
            return WorkerResult(session, {'status': 'done'})
        return WorkerResult(session, dict(status='approved', artifact=state.artifact,
            reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES), findings=[], verified_ids=[]))
    original_run = controller.run
    def controlled(service, delivery_id, **kw):
        with closing(h.connect(None)) as conn:  # the build runs on its own thread
            return original_run(service, delivery_id, conn=conn, execute=worker, **kw)
    monkeypatch.setattr(controller, 'run', controlled)
    monkeypatch.setattr(native, 'SETTLE', 0)  # exercise the background path
    asked = json.loads(model_tools.handle_function_call('cpipe_approve_plan', frozen['approval_arguments'],
        session_id='s1', skip_pre_tool_call_hook=True))
    assert asked['status'] == 'ask_user' and 'No pushes.' in asked['clarify']['question']
    consent = reply(frozen['approval_arguments'], choice, asked['clarify']['question'])
    assert consent['status'] == ('approved' if choice == 'Yes' else 'rejected')
    assert not workers
    out = json.loads(model_tools.handle_function_call('cpipe_execute_intent',
        dict(token=token, operation='build', arguments={'delivery_id': delivery_id}),
        session_id='s1', skip_pre_tool_call_hook=True))
    if choice == 'No':
        assert 'error' in out and not workers
        assert (repo / 'double.py').read_text() == 'def double(n): return n\n'
    else:
        assert out['phase'] == 'running'
        native._RUNS[delivery_id].join(60)
        assert len(told) == 1 and told[0][1] == {'role': 'user', 'session_key': 'chat-one'}
        text = told[0][0]
        assert text.startswith(native.SHOW + f'**cpipe · `{delivery_id}` is done and ready for your review.**')
        assert '| Steps | tests, implement, checks, review ×2 |' in text
        assert '| lint | pass: it still fails, but only on failures' in text
        with native.scope() as (service, _, _home):
            state = service.read(delivery_id)
            out = dict(native.controller.summary(state, service), worktree=state.plan.worktree)
        assert out['phase'] == 'locally_ready'
        assert out['outcomes'] == ['fail', 'pass', 'pass', 'pass', 'pass']
        assert workers == ['implement', 'review', 'review']
        again = call(token, 'build', {'delivery_id': delivery_id})  # the real state, not the cached 'running'
        assert again['phase'] == 'locally_ready' and 'background' not in again
        assert workers == ['implement', 'review', 'review']  # No duplicate launches on retry.
        # Built in a worktree of its own; the investigated checkout is untouched.
        assert (repo / 'double.py').read_text() == 'def double(n): return n\n'
        assert Path(out['worktree']).parent == repo.parent / (repo.name + '-worktrees')  # next to the repository
        assert (Path(out['worktree']) / 'double.py').read_text() == 'def double(n): return n * 2\n'
        assert call(token, 'status', {})['phase'] == 'locally_ready'


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


def test_missing_behavior_check_fails_at_prepare_not_after_approval(run_context):
    run_context[2]['checks'][0]['obligation'] = 'unit'
    out = prepare(run_context)
    assert 'behavior' in json.dumps(out)
    assert out.get('phase') != 'approach_review'


def test_roles_are_asked_once_then_remembered_and_never_guessed(hermes_home, monkeypatch):
    from hermes_cli import profiles
    from cpipe.delivery import approach, live_probe
    from gateway import session_context
    monkeypatch.setattr(session_context, 'get_session_env', lambda key: {'HERMES_SESSION_ID': 's1'}.get(key, 'chat-one'))
    names = ['default', 'forge', 'sentry', 'blank']
    monkeypatch.setattr(profiles, 'list_profile_names', lambda: names)
    def observe(role, name):
        if name == 'blank':
            raise ValueError('no model')
        return Actor(role, 'hermes', name, name + '-model', 'p')
    monkeypatch.setattr(live_probe, 'observe_actor', observe)
    from cpipe import routing
    souls = {'sentry': ('You are Sentry, the reviewer', True), 'forge': ('You are Forge, the coder', False)}
    monkeypatch.setattr(routing, 'soul', lambda name: souls.get(name, ('', False)))
    ctx, queued = Context(), []
    ctx.inject_message = lambda text, **kw: queued.append(text) or True
    native.register(ctx)
    ctx.commands['cp-run']('Fix it')
    assert 'No cpipe roles are saved yet' in queued[-1] and 'forge (forge-model; You are Forge, the coder)' in queued[-1]
    assert 'sentry (sentry-model; read-only, review only; You are Sentry' in queued[-1]
    assert 'blank' not in queued[-1]  # cannot run without a saved model
    approach.remember_roles(hermes_home, {'builder': 'sentry', 'reviewer': 'forge'})
    ctx.commands['cp-run']('Fix it')
    assert 'No cpipe roles are saved yet' in queued[-1]  # a read-only builder is never reused
    approach.remember_roles(hermes_home, {'builder': 'forge', 'reviewer': 'sentry'})
    ctx.commands['cp-run']('Fix it')
    assert 'Use builder ["forge"] and reviewer ["sentry"]' in queued[-1]
    names.remove('sentry')  # a remembered profile disappears: ask again rather than fail later
    ctx.commands['cp-run']('Fix it')
    assert 'No cpipe roles are saved yet' in queued[-1]
    names[:] = ['default', 'blank']
    assert 'two Hermes profiles' in ctx.commands['cp-run']('Fix it')


def test_a_read_only_builder_is_refused_before_approval_and_fallbacks_are_shown(run_context, monkeypatch):
    from cpipe import routing
    monkeypatch.setattr(routing, 'soul', lambda name: ('', name == 'reviewer2'))
    ctx, token, spec, approach_, values, _ = run_context
    out = call(token, 'prepare', dict(spec={**spec, 'builder': ['reviewer2', 'builder']},
                                      approach=approach_, expected_revision=0))
    assert 'reviewer2 is read-only' in out['error']
    out = call(token, 'prepare', dict(spec={**spec, 'builder': ['builder', 'builder2']},
                                      approach=approach_, expected_revision=0))
    assert out['phase'] == 'approach_review'
    assert 'builder: builder (then, if it refuses or keeps failing: builder2)' in Path(out['document_path']).read_text()


def test_continue_restarts_a_run_that_waits_even_after_its_receipt_expired(run_context, repo, hermes_home, monkeypatch):
    import model_tools
    from tools.registry import registry
    from cpipe.delivery import controller
    from cpipe.delivery.review import REVIEW_AXES
    from cpipe.delivery.workers import WorkerResult
    from cpipe.runtime import hermes_api as h
    ctx, token, _, _, _, _ = run_context
    run_context[2]['checks'].append(dict(obligation='lint', argv=['sh', '-c', 'echo "other/x.js 1:1 error"; exit 1'],
                                         timeout_seconds=20))
    prepare(run_context)
    frozen = call(token, 'plan', {'draft_revision': 1})
    delivery_id = frozen['delivery_id']
    monkeypatch.setattr(registry, 'dispatch', lambda name, args, **kw: ctx.tools[name]['handler'](args, **kw))
    h.kb.init_db()
    told, workers, declining, lines = [], [], [True], []
    from hermes_cli import plugin_events
    monkeypatch.setitem(sys.modules, 'tui_gateway.server', object())  # as inside the desktop backend
    monkeypatch.setattr(plugin_events, 'broadcast_plugin_event', lambda plugin, event, payload: lines.append(
        (plugin, event, payload['session_id'], payload['delivery_id'], payload['text'], payload['done'])))
    ctx.inject_message = lambda text, **kw: told.append(text) or True
    def worker(state, *, on_session, **kw):
        workers.append(state.activity)
        session = state.activity + '-fixture'
        on_session(session)
        if state.activity == 'implement':
            if declining[0]:
                return WorkerResult(session, {'status': 'blocked'})
            (Path(state.plan.worktree) / 'double.py').write_text('def double(n): return n * 2\n')
            return WorkerResult(session, {'status': 'done'})
        return WorkerResult(session, dict(status='approved', artifact=state.artifact,
            reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES), findings=[], verified_ids=[]))
    original_run = controller.run
    def controlled(service, delivery_id, **kw):
        with closing(h.connect(None)) as conn:
            return original_run(service, delivery_id, conn=conn, execute=worker, **kw)
    monkeypatch.setattr(controller, 'run', controlled)
    monkeypatch.setattr(native, 'SETTLE', 0)
    def build():
        return json.loads(model_tools.handle_function_call('cpipe_execute_intent',
            dict(token=token, operation='build', arguments={'delivery_id': delivery_id}),
            session_id='s1', skip_pre_tool_call_hook=True))
    reply(frozen['approval_arguments'], 'Yes')
    build()
    native._RUNS[delivery_id].join(60)
    assert call(token, 'status', {})['phase'] == 'waiting'  # the only builder declined
    with Journal.for_profile(hermes_home) as store:  # a day later: the receipt no longer starts work
        row = store.db.execute('SELECT payload FROM intent WHERE id=?', (native._intent_key(token),)).fetchone()
        store.db.execute('UPDATE intent SET payload=? WHERE id=?',
                         (json.dumps({**json.loads(row[0]), 'expires': 0}), native._intent_key(token)))
    kept = list(hermes_home.rglob('baseline/*.json'))
    assert len(kept) == 1
    kept[0].unlink()  # a run from before failures were recorded up front
    declining[0] = False  # the user fixed the cause and says "continue"
    assert build()['phase'] == 'running'
    native._RUNS[delivery_id].join(60)
    assert call(token, 'status', {})['phase'] == 'locally_ready'
    assert workers == ['implement', 'implement', 'review', 'review']
    # The chat sees each handover and recovery as a display-only line, never a model turn.
    assert {(p, e, sid, d) for p, e, sid, d, _, _ in lines} == {('cpipe', 'progress', 's1', delivery_id)}
    texts = [text for *_, text, _ in lines]
    assert texts[:2] == ['running the new test (it must fail first)', 'builder is implementing']
    assert texts[2].startswith('builder declined implement')
    assert 'reviewer is reviewing' in texts and lines[-1][4:] == (f'{delivery_id} is done and ready for your review', True)
    assert 'error' in call(token, 'prepare', {})  # still no new work on an expired receipt


@pytest.fixture
def github(repo, tmp_path, monkeypatch):
    """origin looks like GitHub but pushes go to a local bare repository; gh is a fake that keeps its PRs in a file."""
    bare, prs, bin_dir = tmp_path / 'origin.git', tmp_path / 'prs.json', tmp_path / 'bin'
    subprocess.run(['git', 'init', '-q', '--bare', str(bare)], check=True)
    for args in (('add', 'test_behavior.py'),  # a PR starts from the base as committed
                 ('-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'test: behavior'),
                 ('remote', 'add', 'origin', 'https://github.com/acme/widget.git'),
                 ('config', f'url.{bare}.insteadOf', 'https://github.com/acme/widget.git'),
                 ('update-ref', 'refs/remotes/origin/feature', 'HEAD')):
        subprocess.run(['git', *args], cwd=repo, check=True)
    bin_dir.mkdir()
    gh = bin_dir / 'gh'
    gh.write_text(f'''#!{sys.executable}
import json, subprocess, sys
from pathlib import Path
db, args = Path({str(prs)!r}), sys.argv[1:]
prs = json.loads(db.read_text()) if db.exists() else []
def opt(name): return args[args.index(name) + 1]
def head(b): return subprocess.run(['git', '--git-dir', {str(bare)!r}, 'rev-parse', 'refs/heads/' + b],
                                   capture_output=True, text=True).stdout.strip()
if args[:2] == ['auth', 'status']: sys.exit(0)
if args[:2] == ['pr', 'list']:
    print(json.dumps([dict(p, headRefOid=head(p['headRefName'])) for p in prs if p['headRefName'] == opt('--head')]))
elif args[:2] == ['pr', 'create']:
    if Path({str(tmp_path / 'gh-down')!r}).exists():
        sys.exit('HTTP 503: GitHub is unavailable')
    assert '--draft' in args and opt('--repo') == 'acme/widget'
    prs.append(dict(url='https://github.com/acme/widget/pull/%d' % (len(prs) + 1), isDraft=True, state='OPEN',
                    baseRefName=opt('--base'), headRefName=opt('--head'), title=opt('--title'), body=sys.stdin.read()))
    db.write_text(json.dumps(prs))
elif args[:2] == ['pr', 'edit']:
    pr = next(p for p in prs if p['headRefName'] == args[2])
    pr.update(title=opt('--title'), body=sys.stdin.read())
    db.write_text(json.dumps(prs))
elif args[:2] == ['pr', 'view']:
    pr = next(dict(p, headRefOid=head(p['headRefName'])) for p in prs if p['headRefName'] == args[2])
    git = lambda *a: subprocess.run(['git', '--git-dir', {str(bare)!r}, *a], capture_output=True, text=True).stdout
    base = subprocess.run(['git', 'rev-parse', 'refs/remotes/origin/feature'], cwd={str(repo)!r},
                          capture_output=True, text=True).stdout.strip()
    pr['commits'] = [dict(oid=oid, messageHeadline=git('log', '-1', '--format=%s', oid).strip(),
                          messageBody=git('log', '-1', '--format=%b', oid).strip())
                     for oid in git('rev-list', '--reverse', pr['headRefOid'], '^' + base).split()]
    print(json.dumps(pr))
else:
    sys.exit(2)
''')
    gh.chmod(0o755)
    monkeypatch.setenv('PATH', f"{bin_dir}:{__import__('os').environ['PATH']}")
    return bare, prs


def test_asked_for_a_draft_pr_it_opens_one_after_review_and_retries_only_that_step(
        run_context, repo, hermes_home, monkeypatch, github, tmp_path):
    import model_tools
    from tools.registry import registry
    from cpipe.delivery import controller
    from cpipe.delivery.review import REVIEW_AXES
    from cpipe.delivery.workers import WorkerResult
    from cpipe.runtime import hermes_api as h
    bare, prs = github
    ctx, token, spec, _, _, _ = run_context
    spec.update(draft_pr=True, branch='fix/double-doubles')
    (tmp_path / 'gh-down').touch()  # the first attempt fails on GitHub's side
    out = prepare(run_context)
    assert out.get('phase') == 'approach_review', out
    frozen = call(token, 'plan', {'draft_revision': 1})
    delivery_id = frozen['delivery_id']
    monkeypatch.setattr(registry, 'dispatch', lambda name, args, **kw: ctx.tools[name]['handler'](args, **kw))
    h.kb.init_db()
    told, workers = [], []
    ctx.inject_message = lambda text, **kw: told.append(text) or True
    def worker(state, *, on_session, **kw):
        workers.append(state.activity)
        on_session(state.activity + '-fixture')
        if state.activity == 'implement':
            (Path(state.plan.worktree) / 'double.py').write_text('def double(n): return n * 2\n')
            return WorkerResult(state.activity + '-fixture', {'status': 'done'})
        return WorkerResult(state.activity + '-fixture', dict(status='approved', artifact=state.artifact,
            reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES), findings=[], verified_ids=[]))
    asks = []
    def ask(actor, cwd, prompt, **kw):  # the builder writes the PR the conventions ask for; the reviewer verifies it
        asks.append(actor.role)
        if actor.role == 'builder':
            leak = delivery_id if len(asks) == 1 else ''  # internal data is refused before anything is pushed
            return dict(title='Double numbers correctly', body='Fixes double. ' + leak,
                        commits=[dict(message='fix: double numbers correctly', paths=['double.py'])])
        published = json.loads(prompt.split('\nPublished: ', 1)[1])
        assert published['headRefName'] == 'fix/double-doubles' and published['commits'][0]['files'] == ['double.py']
        if asks.count('reviewer') == 1:
            return dict(status='changes_requested', problems=['the body must say how it was verified'])
        assert 'the body must say how it was verified' in asks_prompts[-1]
        return dict(status='approved')
    asks_prompts = []
    def recorded(actor, cwd, prompt, **kw):
        asks_prompts.append(prompt) if actor.role == 'builder' else None
        return ask(actor, cwd, prompt, **kw)
    from cpipe.delivery import hermes_worker
    monkeypatch.setattr(hermes_worker, 'ask', recorded)
    original_run = controller.run
    def controlled(service, delivery_id, **kw):
        with closing(h.connect(None)) as conn:
            return original_run(service, delivery_id, conn=conn, execute=worker, **kw)
    monkeypatch.setattr(controller, 'run', controlled)
    monkeypatch.setattr(native, 'SETTLE', 0)
    asked = json.loads(model_tools.handle_function_call('cpipe_approve_plan', frozen['approval_arguments'],
        session_id='s1', skip_pre_tool_call_hook=True))['clarify']['question']
    assert 'Push the branch fix/double-doubles to origin' in asked and 'Open a draft pull request against feature' in asked
    assert 'No pushes.' not in asked
    assert reply(frozen['approval_arguments'], 'Yes', asked)['status'] == 'approved'

    def build():
        out = call(token, 'build', {'delivery_id': delivery_id})
        if delivery_id in native._RUNS:
            native._RUNS.pop(delivery_id).join(60)
        return out
    build()
    first = told[-1]
    assert first.startswith(native.SHOW + f'**cpipe · `{delivery_id}` is verified and reviewed, but its draft pull request')
    assert '| Pull request |' not in first and '**continue**' in first
    assert any('draft pull request is not done' in r and 'HTTP 503' in r for r in first.splitlines())
    assert workers == ['implement', 'review', 'review']

    (tmp_path / 'gh-down').unlink()  # fixed; "continue" retries the PR step alone
    build()
    done = told[-1]
    assert done.startswith(native.SHOW + f'**cpipe · `{delivery_id}` is done: its draft pull request is open')
    assert '| Pull request | https://github.com/acme/widget/pull/1 |' in done and '**continue**' not in done
    assert workers == ['implement', 'review', 'review']  # no rebuild or second review
    pr, = json.loads(prs.read_text())
    assert pr['title'] == 'Double numbers correctly' and pr['baseRefName'] == 'feature' and delivery_id not in pr['body']
    # GitHub down: content refused (leaked id) and rewritten, push fails. Continue: written, published, verified,
    # rewritten for the verifier's problem, verified.
    assert asks == ['builder', 'builder', 'builder', 'reviewer', 'builder', 'reviewer']
    git = lambda *a: subprocess.run(['git', '--git-dir', str(bare), *a], capture_output=True, text=True,
                                    check=True).stdout.strip()
    branch = 'refs/heads/fix/double-doubles'
    assert git('log', '-1', '--format=%s', branch) == 'fix: double numbers correctly'
    assert git('show', f'{branch}:double.py') == 'def double(n): return n * 2'
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert git('rev-parse', branch + '^') == head  # exactly one commit on the base
    assert (repo / 'double.py').read_text() == 'def double(n): return n\n'  # the checkout is untouched


def test_a_draft_pr_that_could_not_be_opened_is_refused_before_approval(run_context, repo):
    run_context[2].update(draft_pr=True, branch='fix/x')  # no origin remote
    assert 'origin' in prepare(run_context)['error']


@pytest.mark.parametrize('change', [dict(draft_pr=True), dict(branch='fix/x'), dict(draft_pr=True, branch='')])
def test_a_draft_pr_needs_its_own_branch_name_and_a_branch_needs_a_pr(run_context, repo, change):
    run_context[2].update(change)
    assert 'branch' in prepare(run_context)['error']


def test_a_receipt_in_use_keeps_working_for_follow_ups_in_the_same_chat(run_context, hermes_home):
    token = run_context[1]
    def expires(value=None):
        with Journal.for_profile(hermes_home) as store:
            row = store.db.execute('SELECT payload FROM intent WHERE id=?', (native._intent_key(token),)).fetchone()
            if value is not None:
                store.db.execute('UPDATE intent SET payload=? WHERE id=?',
                                 (json.dumps({**json.loads(row[0]), 'expires': value}), native._intent_key(token)))
            return json.loads(row[0])['expires']
    expires(time.time() + 60)  # nearly a day after /cp-run
    assert 'error' not in call(token, 'prepare', dict(spec=run_context[2], approach=run_context[3],
                                                      expected_revision=0, task='redo'))
    assert expires() > time.time() + native.RUN_RECEIPT / 2
