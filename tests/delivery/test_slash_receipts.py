"""Production slash registration, receipt ownership and failure behavior."""
import json
import re

from cpipe import native
from cpipe.delivery.journal import Journal
from cpipe.delivery.service import Event, Service
from .test_artifact import repo
from .test_check_runner import setup_check
from .test_entrypoints import Context


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
    # Already-issued internal plan receipts remain readable; not a registered slash command.
    assert native.dispatch(ctx, 'cp-plan', 'Fix approved behavior') == 'cpipe: /cp-plan queued in this session.'
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
    assert 'no work started' in ctx.commands['cp-run']('Do nothing')
    with Journal.for_profile(hermes_home) as store:
        assert store.db.execute('SELECT COUNT(*) FROM intent').fetchone()[0] == 0


def test_cli_status_uses_the_same_store(repo, tmp_path, hermes_home, capsys):
    from cpipe.runtime.cli import main
    journal, _, _, _ = setup_check(repo, tmp_path)
    proposal = Event.from_dict(journal.event('d1', 'proposal')['fact'])
    journal.db.close()
    with Journal.for_profile(hermes_home) as store:
        Service(store).accept(proposal)
    assert main(['status']) == 0
    out = json.loads(capsys.readouterr().out)
    assert [d['delivery_id'] for d in out['deliveries']] == ['d1']


def runner(home, delivery_id):
    with Journal.for_profile(home) as store:
        row = store.db.execute('SELECT payload FROM intent WHERE id=?', ('runner:' + delivery_id,)).fetchone()
        return json.loads(row[0]) if row else None


def test_a_background_run_that_keeps_crashing_is_parked_retried_then_reported(hermes_home, monkeypatch):
    import time
    from cpipe.delivery.journal import encoded
    calls, told = [], []
    active = type('S', (), dict(version=1, activity='implement', approved=True))()
    monkeypatch.setattr(native, 'RETRY_PAUSE', 0)
    monkeypatch.setattr(native.controller, 'owned', lambda *a: active)
    monkeypatch.setattr(native.controller, 'ready', lambda *a: None)
    monkeypatch.setattr(native.controller, 'run', lambda *a, **kw: calls.append(1) or 1 / 0)
    ctx = Context()
    ctx.inject_message = lambda text, **kw: told.append((text, kw)) or True
    native.register(ctx)
    assert native.launch('d9', session_id='s1', profile_id='default', chat_key='chat-one')['phase'] == 'running'
    native._RUNS['d9'].join(30)
    assert len(calls) == native.CRASH_RETRIES + 1
    assert told == [(told[0][0], {'role': 'user', 'session_key': 'chat-one'})]
    assert 'hit a problem it could not fix right away' in told[0][0] and '| Error | ZeroDivisionError |' in told[0][0]
    record = runner(hermes_home, 'd9')
    assert record['retry_at'] > time.time() and record['parks'] == 1
    native.resume_orphans()
    assert runner(hermes_home, 'd9') == record  # not due yet
    with Journal.for_profile(hermes_home) as store:  # due now; meanwhile the delivery settled
        store.db.execute('UPDATE intent SET payload=? WHERE id=?', (encoded({**record, 'retry_at': 1}), 'runner:d9'))
    monkeypatch.setattr(native.controller, 'owned', lambda *a: type('S', (), dict(version=2, activity='',
                                                                                    approved=True))())
    monkeypatch.setattr(native, '_SCANNED', [0.0])
    native.resume_orphans()
    native._RUNS['d9'].join(30)
    assert len(told) == 2 and told[1][0].startswith(native.SHOW + '**cpipe · `d9` is stopped unexpectedly (ZeroDivisionError).**')
    assert runner(hermes_home, 'd9') is None


def test_an_undelivered_message_is_kept_shown_and_redelivered(hermes_home, monkeypatch):
    told, reachable = [], [False]
    monkeypatch.setattr(native.controller, 'ready', lambda *a: None)
    monkeypatch.setattr(native.controller, 'run', lambda *a, **kw: {'phase': 'locally_ready'})
    monkeypatch.setattr(native.controller, 'owned', lambda *a: (_ for _ in ()).throw(ValueError('gone')))
    ctx = Context()
    ctx.inject_message = lambda text, **kw: told.append(text) or reachable[0]
    native.register(ctx)
    native.launch('d5', session_id='s1', profile_id='default', chat_key='chat-one')
    native._RUNS['d5'].join(30)
    assert len(told) == 1
    with Journal.for_profile(hermes_home) as store:
        assert store.db.execute("SELECT count(*) FROM intent WHERE id='notice:d5'").fetchone()[0] == 1
    reachable[0] = True
    monkeypatch.setattr(native, '_SCANNED', [0.0])
    native.resume_orphans()  # the watchdog or the next chat turn
    assert len(told) == 2 and told[1] == told[0]
    with Journal.for_profile(hermes_home) as store:
        assert store.db.execute("SELECT count(*) FROM intent WHERE id='notice:d5'").fetchone()[0] == 0


def test_build_reports_a_run_that_ends_at_once_in_its_own_reply(hermes_home, monkeypatch):
    told = []
    monkeypatch.setattr(native.controller, 'ready', lambda *a: None)
    monkeypatch.setattr(native.controller, 'run', lambda *a, **kw: {'phase': 'waiting'})
    monkeypatch.setattr(native.controller, 'owned', lambda *a: (_ for _ in ()).throw(ValueError('gone')))
    ctx = Context()
    ctx.inject_message = lambda text, **kw: told.append(text) or True
    native.register(ctx)
    out = native.launch('d4', session_id='s1', profile_id='default', chat_key='chat-one', wait=10)
    assert out['phase'] == 'waiting' and out['message'].startswith('The run is already stopped: it needs you')
    assert told == [] and runner(hermes_home, 'd4') is None


def test_only_long_lived_hermes_processes_host_runs():
    for argv, short in ((['hermes'], False), (['hermes', 'gateway', 'run'], False), (['hermes', '-z', 'hi'], True),
                        (['hermes', 'chat', '--query-file', '-', '--oneshot'], True), (['hermes', 'chat', '-q', 'x'], True)):
        assert native.short_lived(argv, {}) is short
    assert native.short_lived(['hermes'], {'HERMES_KANBAN_TASK': 't1'}) is True


def test_a_run_left_by_a_dead_hermes_process_is_resumed_once(hermes_home, monkeypatch):
    import os
    import socket
    import subprocess
    from cpipe.delivery.native_bridge import alive
    from cpipe.delivery.journal import encoded
    host = socket.gethostname() or 'unknown'
    gone = subprocess.Popen(['true'])
    gone.wait()
    dead, live, mine = f'{host}:{gone.pid}:run', f'{host}:{os.getppid()}:run', f'{host}:{os.getpid()}:run'
    assert not alive(dead) and alive(live) and alive(f'other-host:{gone.pid}:run')
    with Journal.for_profile(hermes_home) as store:
        # d6 belongs to this very process, but no thread runs it any more (it died).
        for delivery_id, claimer in (('d8', dead), ('d7', live), ('d6', mine)):
            store.db.execute('INSERT INTO intent(id,payload,acknowledged) VALUES (?,?,1)', (
                'runner:' + delivery_id, encoded(dict(claimer=claimer, session_id='s1', profile_id='default',
                                                      chat_key='chat-one'))))
    runs = []
    monkeypatch.setattr(native.controller, 'ready', lambda *a: None)
    monkeypatch.setattr(native.controller, 'run', lambda service, delivery_id, **kw: runs.append(delivery_id) or {})
    ctx = Context()
    ctx.inject_message = lambda *a, **kw: True
    native.register(ctx)
    monkeypatch.setattr(native, '_SCANNED', [0.0])
    native.resume_orphans()
    native._RUNS['d8'].join(30)
    native._RUNS['d6'].join(30)
    native.resume_orphans()  # throttled; and the finished runs removed their records
    assert sorted(runs) == ['d6', 'd8']
    with Journal.for_profile(hermes_home) as store:
        assert [r[0] for r in store.db.execute("SELECT id FROM intent WHERE id LIKE 'runner:%'")] == ['runner:d7']


def test_a_second_run_in_the_same_worktree_is_refused(hermes_home, monkeypatch):
    import pytest
    from types import SimpleNamespace
    from cpipe.delivery.journal import encoded
    fake = lambda worktree: SimpleNamespace(approved=True, activity='implement', plan=SimpleNamespace(worktree=worktree))
    states = {'busy': fake('/w/one'), 'same': fake('/w/one'), 'other': fake('/w/two')}
    monkeypatch.setattr(Service, 'read', lambda self, delivery_id: states.get(delivery_id))
    monkeypatch.setattr(native.controller, 'ready', lambda *a: None)
    monkeypatch.setattr(native.controller, 'run', lambda *a, **kw: {})
    monkeypatch.setattr(native.controller, 'owned', lambda *a: (_ for _ in ()).throw(ValueError('settled')))
    with Journal.for_profile(hermes_home) as store:
        store.db.execute('INSERT INTO intent(id,payload,acknowledged) VALUES (?,?,1)', ('runner:busy', encoded({})))
    with pytest.raises(ValueError, match='another cpipe run is working in this worktree'):
        native.launch('same', session_id='s1', profile_id='default', chat_key='chat-one')
    native.launch('other', session_id='s1', profile_id='default', chat_key='chat-one')
    native._RUNS['other'].join(30)
    assert not native._RUNS['other'].is_alive()


def test_a_repeated_build_restarts_a_run_nothing_is_working_on(hermes_home, monkeypatch):
    runs = []
    active = type('S', (), dict(version=1, phase='implementing', activity='implement', approved=True))()
    monkeypatch.setattr(native.controller, 'owned', lambda *a: active)
    monkeypatch.setattr(native.controller, 'summary', lambda state, service=None: {'phase': 'implementing'})
    monkeypatch.setattr(native.controller, 'ready', lambda *a: None)
    monkeypatch.setattr(native.controller, 'run', lambda service, delivery_id, **kw: runs.append(delivery_id) or {})
    monkeypatch.setattr(native, 'RETRY_PAUSE', 0)
    out = native.ensure_running('d3', session_id='s1', profile_id='default', chat_key='chat-one')
    assert out['phase'] == 'running'  # nothing ran it (e.g. its process died): started again
    native._RUNS['d3'].join(30)
    assert runs[0] == 'd3'
