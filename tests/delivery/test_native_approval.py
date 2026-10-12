"""Consent is the user's own clarify answer to the exact frozen plan, revalidated before it counts."""
import json

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


def ids(state):
    return dict(session_id=state.plan.envelope.session_id, profile_id=state.plan.envelope.profile_id)


def ask(service, state):
    from cpipe.delivery.approval import request
    return request(service, arguments(state), **ids(state))


def answer(service, state, response, asked=None):
    from cpipe.delivery.approval import answer
    asked = asked or ask(service, state)['clarify']['question']
    return answer(service, arguments(state), **ids(state), asked=asked, response=response)


@pytest.mark.parametrize('response, status', [('Yes', 'approved'), (' yes ', 'approved'), ('No', 'rejected'),
                                              ('Use a list instead', 'changes_requested')])
def test_the_answer_decides_and_is_kept_without_the_users_text(repo, tmp_path, response, status):
    journal, service, state = proposed(repo, tmp_path)
    question = ask(service, state)
    assert question['status'] == 'ask_user' and question['clarify']['choices'] == ['Yes', 'No']
    text = question['clarify']['question']
    assert text.startswith('Ready to start?\n\n' + state.plan.goal)
    for part in ('Scope\n', 'Verification\n', 'Limits\n', 'not sandboxed', 'No pushes.',
                 'Delivery: ' + state.plan.envelope.delivery_id, 'Plan revision: ' + arguments(state)['plan_revision'],
                 *state.plan.acceptance, *state.plan.non_goals, *state.plan.write_paths):
        assert part in text
    assert '"schema"' not in text
    assert answer(service, state, response)['status'] == status
    assert service.read(state.plan.envelope.delivery_id).approved is (status == 'approved')
    journal.db.close()
    with Journal(tmp_path / 'approval.db') as reopened:
        assert ask(Service(reopened), state)['status'] == status  # recorded once; never asked again
        assert answer(Service(reopened), state, 'Yes', text) is None
        assert 'instead' not in json.dumps(reopened.db.execute('SELECT payload FROM intent').fetchall())


def test_only_the_exact_plan_text_can_be_answered(repo, tmp_path):
    journal, service, state = proposed(repo, tmp_path)
    with journal:
        text = ask(service, state)['clarify']['question']
        assert answer(service, state, 'Yes', text.replace('\n', '\n\n  ')) is not None  # layout may differ
    journal, service, state = proposed(repo, tmp_path / 'other')
    with journal:
        text = ask(service, state)['clarify']['question']
        assert answer(service, state, 'Yes', text.replace('Scope', 'Scope (trimmed)')) is None
        assert answer(service, state, 'Yes', 'Ready to start?') is None
        assert not service.read(state.plan.envelope.delivery_id).approved


def test_changed_artifact_before_the_answer_does_not_approve(repo, tmp_path):
    journal, service, state = proposed(repo, tmp_path)
    with journal:
        text = ask(service, state)['clarify']['question']
        (tmp_path / 'repo' / 'double.py').write_text('def double(x): return x * 2\n')
        assert answer(service, state, 'Yes', text) is None
        assert not service.read(state.plan.envelope.delivery_id).approved


def test_cancellation_or_expiry_before_the_answer_cannot_grant_authority(repo, tmp_path, monkeypatch):
    import cpipe.delivery.approval as adapter
    journal, service, state = proposed(repo, tmp_path)
    with journal:
        text = ask(service, state)['clarify']['question']
        monkeypatch.setattr(adapter.time, 'time', lambda: state.plan.expires_at)
        assert answer(service, state, 'Yes', text) is None
        monkeypatch.undo()
        service.accept(Event('cancel', 'cancelled', state.plan.envelope.delivery_id,
            state.plan.digest(), state.artifact, state.version, state.last_at, **ids(state)))
        assert answer(service, state, 'Yes', text) is None
        assert service.read(state.plan.envelope.delivery_id).phase == 'cancelled'


def test_unbound_or_model_supplied_consent_is_rejected(repo, tmp_path):
    from cpipe.delivery.approval import request, answer as record
    journal, service, state = proposed(repo, tmp_path)
    with journal:
        args = arguments(state)
        sid, pid = state.plan.envelope.session_id, state.plan.envelope.profile_id
        for bad_args, session, profile in [
            ({**args, 'approved': True}, sid, pid), (args, 'other-session', pid),
            (args, sid, 'other-profile'), ({**args, 'plan_revision': 'wrong'}, sid, pid),
        ]:
            assert 'error' in request(service, bad_args, session_id=session, profile_id=profile)
        text = ask(service, state)['clarify']['question']
        assert record(service, args, session_id='other-session', profile_id=pid, asked=text, response='Yes') is None
        assert not service.read(state.plan.envelope.delivery_id).approved


def clarify(questions, answers, outcome='submitted'):
    """What Hermes' clarify tool returns once the user answered in the chat."""
    from tools.clarify_tool import clarify_tool
    return clarify_tool(questions, callback=lambda normalized: {'answers': answers, 'outcome': outcome})


def test_hermes_clarify_answer_reaches_cpipe_through_the_post_tool_call_hook(repo, tmp_path, hermes_home, monkeypatch):
    """The real Hermes hook path: the model cannot write a clarify result, so it cannot approve."""
    from cpipe import native
    from hermes_cli import plugins
    from model_tools import _emit_post_tool_call_hook
    journal, service, state = proposed(repo, tmp_path)
    proposal = Event.from_dict(journal.event(state.plan.envelope.delivery_id, 'proposal')['fact'])
    journal.db.close()
    with Journal.for_profile(hermes_home) as scoped:
        Service(scoped).accept(proposal)
    registered, hooks = {}, {}
    class Context:
        def register_tool(self, **kw):
            registered[kw['name']] = kw['handler']
        def register_hook(self, name, handler):
            hooks.setdefault(name, []).append(handler)
    native.register(Context())
    monkeypatch.setattr(plugins, 'has_hook', lambda name: name in hooks)
    monkeypatch.setattr(plugins, 'invoke_hook', lambda name, **kw: [h(**kw) for h in hooks.get(name, [])])
    sid = state.plan.envelope.session_id
    question = json.loads(registered['cpipe_approve_plan'](arguments(state), session_id=sid))['clarify']
    questions = [{'question': 'Which color?', 'choices': ['Blue', 'Red']}, question]
    for outcome in ('cancelled', 'timed_out'):  # nothing the user submitted: nothing changes
        _emit_post_tool_call_hook(function_name='clarify', function_args={'questions': questions},
                                  result=clarify(questions, {'q1': 'Yes'}, outcome), session_id=sid)
    _emit_post_tool_call_hook(function_name='not_clarify', function_args={'questions': questions},
                              result=clarify(questions, {'q1': 'Yes'}), session_id=sid)
    with Journal.for_profile(hermes_home) as scoped:
        assert not Service(scoped).read(state.plan.envelope.delivery_id).approved
    _emit_post_tool_call_hook(function_name='clarify', function_args={'questions': questions},
                              result=clarify(questions, {'q0': 'Blue', 'q1': 'Yes'}), session_id=sid)
    with Journal.for_profile(hermes_home) as scoped:
        assert Service(scoped).read(state.plan.envelope.delivery_id).approved
    assert json.loads(registered['cpipe_approve_plan'](arguments(state), session_id=sid))['status'] == 'approved'
