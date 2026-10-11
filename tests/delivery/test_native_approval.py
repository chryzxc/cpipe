"""Native transport consent is a fact only after frozen authority revalidation."""
import json
from dataclasses import replace

import pytest

from cpipe.delivery.service import Event, Service
from cpipe.delivery.journal import Journal
from .test_check_runner import setup_check
from .test_artifact import repo


def proposed(repo, tmp_path):
    setup_journal, service, state, _ = setup_check(repo, tmp_path)
    setup_journal.db.close()
    # setup_check approved its own fixture. Use a separate real proposal journal.
    plan = state.plan
    journal = Journal(tmp_path / 'approval.db')
    service = Service(journal)
    state, _ = service.accept(Event('proposal', 'proposal_created', plan.envelope.delivery_id,
        plan.digest(), state.artifact, 0, state.started_at,
        session_id=plan.envelope.session_id, profile_id=plan.envelope.profile_id, plan=plan))
    return journal, service, state


def arguments(state):
    from cpipe.delivery.approval import arguments
    return arguments(state)


def request(service, args, state, **kw):
    from cpipe.delivery.approval import request
    return request(service, args, session_id=state.plan.envelope.session_id,
        profile_id=state.plan.envelope.profile_id, **kw)


@pytest.mark.parametrize('choice', ['once', 'deny'])
def test_real_gateway_accept_or_reject_and_restart_does_not_reprompt(repo, tmp_path, monkeypatch, choice):
    from tools import approval
    from tools.approval_context import set_current_session_key, reset_current_session_key
    from cpipe import plan_approval
    monkeypatch.setattr(plan_approval, '_no_user_can_answer', lambda: False)
    journal, service, state = proposed(repo, tmp_path)
    seen = []
    def notify(payload):
        seen.append(payload)
        assert not payload['allow_session'] and not payload['allow_permanent']
        assert 'not sandboxed' in payload['description']
        assert state.plan.goal == payload['command']
        assert payload['title'] == 'Ready to start?'
        assert 'Scope\n' in payload['description']
        assert 'Verification\n' in payload['description']
        assert 'Limits\n' in payload['description']
        assert '"schema"' not in payload['description']
        for item in state.plan.acceptance + state.plan.non_goals + state.plan.write_paths:
            assert item in payload['description']
        assert approval.resolve_gateway_approval('cp-fixture', choice, request_id=payload['request_id'])
    approval.register_gateway_notify('cp-fixture', notify)
    token = set_current_session_key('cp-fixture')
    try:
        args = arguments(state)
        result = request(service, args, state)
        assert result['status'] == ('approved' if choice == 'once' else 'rejected')
        assert service.read(state.plan.envelope.delivery_id).approved is (choice == 'once')
        journal.db.close()
        with Journal(tmp_path / 'approval.db') as reopened:
            assert request(Service(reopened), args, state) == result
        assert len(seen) == 1
    finally:
        approval.unregister_gateway_notify('cp-fixture')
        reset_current_session_key(token)


@pytest.mark.parametrize('decision', [
    {'resolved': False, 'choice': None},
    {'resolved': True, 'choice': 'session'},
    {'resolved': True, 'choice': 'permanent'},
    {'resolved': True, 'choice': 'once', 'cancelled': True},
    {'resolved': True, 'choice': 'once', 'notify_failed': True},
    {'resolved': True, 'choice': 'once', 'unavailable': True},
])
def test_only_one_operation_transport_consent_can_approve(repo, tmp_path, monkeypatch, decision):
    from cpipe import plan_approval
    journal, service, state = proposed(repo, tmp_path)
    with journal:
        monkeypatch.setattr(plan_approval, '_native_prompt', lambda *a: decision)
        assert request(service, arguments(state), state)['status'] == 'waiting_for_approval'
        assert not service.read(state.plan.envelope.delivery_id).approved


def test_changed_artifact_during_open_ui_does_not_approve(repo, tmp_path, monkeypatch):
    from cpipe import plan_approval
    journal, service, state = proposed(repo, tmp_path)
    def prompt(*a):
        (tmp_path / 'repo' / 'double.py').write_text('def double(x): return x * 2\n')
        return {'resolved': True, 'choice': 'once'}
    with journal:
        monkeypatch.setattr(plan_approval, '_native_prompt', prompt)
        assert request(service, arguments(state), state)['status'] == 'stale_plan'
        assert not service.read(state.plan.envelope.delivery_id).approved


def test_cancellation_during_ui_cannot_resurrect_authority(repo, tmp_path, monkeypatch):
    from cpipe import plan_approval
    journal, service, state = proposed(repo, tmp_path)
    def prompt(*a):
        service.accept(Event('cancel', 'cancelled', state.plan.envelope.delivery_id,
            state.plan.digest(), state.artifact, state.version, state.last_at,
            session_id=state.plan.envelope.session_id, profile_id=state.plan.envelope.profile_id))
        return {'resolved': True, 'choice': 'once'}
    with journal:
        monkeypatch.setattr(plan_approval, '_native_prompt', prompt)
        assert request(service, arguments(state), state)['status'] == 'stale_plan'
        assert service.read(state.plan.envelope.delivery_id).phase == 'cancelled'


def test_expiry_during_ui_cannot_return_approved(repo, tmp_path, monkeypatch):
    from cpipe import plan_approval
    import cpipe.delivery.approval as adapter
    journal, service, state = proposed(repo, tmp_path)
    def prompt(*a):
        monkeypatch.setattr(adapter.time, 'time', lambda: state.plan.expires_at)
        return {'resolved': True, 'choice': 'once'}
    with journal:
        monkeypatch.setattr(plan_approval, '_native_prompt', prompt)
        assert request(service, arguments(state), state)['status'] == 'stale_plan'
        assert not service.read(state.plan.envelope.delivery_id).approved


def test_unbound_or_model_supplied_consent_is_rejected_without_ui(repo, tmp_path, monkeypatch):
    from cpipe import plan_approval
    from cpipe.delivery.approval import request as call
    journal, service, state = proposed(repo, tmp_path)
    def unexpected(*a):
        pytest.fail('invalid authority must not open UI')
    with journal:
        monkeypatch.setattr(plan_approval, '_native_prompt', unexpected)
        args = arguments(state)
        sid, pid = state.plan.envelope.session_id, state.plan.envelope.profile_id
        for bad_args, session, profile in [
            ({**args, 'approved': True}, sid, pid), (args, 'other-session', pid),
            (args, sid, 'other-profile'), ({**args, 'plan_revision': 'wrong'}, sid, pid),
        ]:
            assert 'error' in call(service, bad_args, session_id=session, profile_id=profile)
        assert not service.read(state.plan.envelope.delivery_id).approved


def test_transport_exception_is_redacted_and_does_not_retry_implicitly(repo, tmp_path, monkeypatch):
    from cpipe import plan_approval
    journal, service, state = proposed(repo, tmp_path)
    calls = []
    secret = 'fixture-credential-value'
    def prompt(*a):
        calls.append(1)
        raise RuntimeError(secret)
    with journal:
        monkeypatch.setattr(plan_approval, '_native_prompt', prompt)
        args = arguments(state)
        result = request(service, args, state)
        assert result['status'] == 'waiting_for_approval'
        assert request(service, args, state) == result
        assert len(calls) == 1
        assert secret not in json.dumps(journal.db.execute('SELECT payload FROM intent').fetchall())
        assert not service.read(state.plan.envelope.delivery_id).approved


@pytest.mark.parametrize('choice', ['once', 'deny'])
def test_registered_tool_calls_native_gateway_for_profile_scoped_plan(repo, tmp_path, hermes_home, monkeypatch, choice):
    from cpipe import native, plan_approval
    from tools import approval
    from tools.approval_context import set_current_session_key, reset_current_session_key
    journal, service, state = proposed(repo, tmp_path)
    proposal = Event.from_dict(journal.event(state.plan.envelope.delivery_id, 'proposal')['fact'])
    journal.db.close()
    with Journal.for_profile(hermes_home) as scoped:
        Service(scoped).accept(proposal)
    monkeypatch.setattr(plan_approval, '_no_user_can_answer', lambda: False)
    seen = []
    def notify(payload):
        seen.append(payload)
        assert approval.resolve_gateway_approval('cp-tool-fixture', choice, request_id=payload['request_id'])
    approval.register_gateway_notify('cp-tool-fixture', notify)
    token = set_current_session_key('cp-tool-fixture')
    try:
        import model_tools
        from tools.registry import registry
        from hermes_cli import plugins
        registered, hooks = {}, {}
        class Context:
            def register_tool(self, **kw):
                registered[kw['name']] = kw['handler']
            def register_hook(self, name, handler):
                hooks[name] = handler
        native.register(Context())
        monkeypatch.setattr(plugins, 'has_hook', lambda name: name in hooks)
        monkeypatch.setattr(plugins, 'invoke_hook', lambda name, **kw: [hooks[name](**kw)] if name in hooks else [])
        monkeypatch.setattr(registry, 'dispatch', lambda name, args, **kw: registered[name](args, **kw))
        result = json.loads(model_tools.handle_function_call('cpipe_approve_plan', arguments(state),
            session_id=state.plan.envelope.session_id, skip_pre_tool_call_hook=True))
        assert result['status'] == ('approved' if choice == 'once' else 'rejected')
        assert len(seen) == 1
        with Journal.for_profile(hermes_home) as scoped:
            current = Service(scoped).read(state.plan.envelope.delivery_id)
            assert current.approved is (choice == 'once')
    finally:
        approval.unregister_gateway_notify('cp-tool-fixture')
        reset_current_session_key(token)
