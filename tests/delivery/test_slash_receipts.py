"""Production slash registration, receipt ownership and failure behavior."""
import json
import re

from cpipe import native
from cpipe.delivery.journal import Journal
from cpipe.delivery.service import Event, Service
from .test_artifact import repo
from .test_check_runner import setup_check
from .test_v2_entrypoints import Context


def test_slash_receipt_routes_plan_and_fences_session(repo, tmp_path, hermes_home, monkeypatch):
    from gateway import session_context
    journal, _, state, _ = setup_check(repo, tmp_path)
    proposal = Event.from_dict(journal.event('d1', 'proposal')['fact'])
    journal.db.close()
    values = {'HERMES_SESSION_ID': 's1', 'HERMES_SESSION_KEY': 'chat-one'}
    monkeypatch.setattr(session_context, 'get_session_env', lambda key: values.get(key, ''))
    def propose(service, payload, **kw):
        assert kw == {'session_id': 's1', 'profile_id': 'default'}
        return service.accept(proposal)[0]
    monkeypatch.setattr(native.controller, 'from_spec', propose)
    queued = []
    ctx = Context()
    ctx.inject_message = lambda text, **kw: queued.append((text, kw)) or True
    native.register(ctx)
    assert ctx.commands['cp-plan']('Fix approved behavior') == 'cpipe v2: /cp-plan queued in this session.'
    assert queued[0][1] == {'role': 'user', 'session_key': 'chat-one'}
    token = re.search(r'using token ([A-Za-z0-9_-]+), operation', queued[0][0]).group(1)
    args = dict(token=token, operation='plan', arguments={})
    result = json.loads(ctx.tools['cpipe_execute_intent']['handler'](args, session_id='s1'))
    assert result['delivery_id'] == 'd1'
    assert result['phase'] == 'proposal'
    assert result['approved'] is False
    assert result['approval_arguments']['delivery_id'] == 'd1'
    assert json.loads(native.execute(args, session_id='s1')) == result
    values['HERMES_SESSION_KEY'] = 'other-chat'
    assert 'error' in json.loads(native.execute(args, session_id='s1'))
    values['HERMES_SESSION_KEY'] = 'chat-one'
    assert 'error' in json.loads(native.execute(args, session_id='wrong-session'))
    # The same receipt cannot bypass actual controller consent validation.
    assert 'error' in json.loads(native.execute(dict(token=token, operation='build',
                                    arguments={'delivery_id': 'd1'}), session_id='s1'))
    with Journal.for_profile(hermes_home) as store:
        assert Service(store).read('d1').approved is False


def test_failed_injection_leaves_no_receipt(hermes_home, monkeypatch):
    from gateway import session_context
    monkeypatch.setattr(session_context, 'get_session_env', lambda key: 's1')
    ctx = Context()
    ctx.inject_message = lambda *a, **kw: False
    native.register(ctx)
    assert 'no work started' in ctx.commands['cp-plan']('Do nothing')
    with Journal.for_profile(hermes_home) as store:
        assert store.db.execute('SELECT COUNT(*) FROM intent').fetchone()[0] == 0


def test_cli_status_uses_the_same_v2_store(repo, tmp_path, hermes_home, capsys):
    from cpipe.runtime.cli import main
    journal, _, _, _ = setup_check(repo, tmp_path)
    proposal = Event.from_dict(journal.event('d1', 'proposal')['fact'])
    journal.db.close()
    with Journal.for_profile(hermes_home) as store:
        Service(store).accept(proposal)
    assert main(['status']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['engine_version'] == 2
    assert [d['delivery_id'] for d in out['deliveries']] == ['d1']
