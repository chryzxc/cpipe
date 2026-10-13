"""Public command names, help and responses use plain cpipe terminology."""
import argparse
import json
import re

import pytest

from cpipe import native
from cpipe.runtime.cli import main, setup_parser
from .test_entrypoints import Context


def plain(text):
    text = re.sub(r'token [A-Za-z0-9_-]+', 'token', text)  # random receipt tokens may contain any letters
    assert 'v2' not in text.lower()
    assert 'legacy' not in text.lower()


def test_registered_command_and_tool_help_is_plain():
    import cpipe
    ctx = Context()
    descriptions = []
    register = ctx.register_command
    def record(name, handler, **kw):
        descriptions.append(kw['description'])
        register(name, handler, **kw)
    ctx.register_command = record
    cpipe.register(ctx)
    plain(json.dumps(descriptions))
    plain(ctx.cli['cpipe']['help'])
    plain(ctx.cli['cpipe']['description'])
    plain(ctx.tools['cpipe_execute_intent']['schema']['description'])


def test_cli_help_and_names_are_plain():
    parser = argparse.ArgumentParser()
    setup_parser(parser)
    plain(parser.format_help())
    assert parser.parse_args(['smoke']).wr_command == 'smoke'
    args = parser.parse_args(['live-probe', '--builder', 'forge', '--reviewer', 'sentry'])
    assert args.wr_command == 'live-probe'
    assert (args.builder, args.reviewer, args.seconds) == ('forge', 'sentry', 480)


def test_doctor_response_is_plain(capsys):
    assert main(['doctor']) == 0
    out = capsys.readouterr().out
    plain(out)
    assert json.loads(out)['native_interfaces'] is True
    assert json.loads(out)['warnings'] == []


def test_doctor_warns_when_something_else_takes_cpipe_cards(board, capsys):
    from cpipe.runtime import hermes_api as h
    tid = h.kb.create_task(board, title='cpipe: implement', assignee='forge', created_by='cpipe')
    assert h.claim_task(board, tid, claimer='otherhost:1:wr-x', ttl_seconds=600)
    assert main(['doctor']) == 0
    assert 'claimed by something else' in json.loads(capsys.readouterr().out)['warnings'][0]


@pytest.mark.parametrize('session_id,injection', [('', True), ('s1', False), ('s1', True)])
def test_run_responses_and_injected_prompt_are_plain(hermes_home, monkeypatch, session_id, injection):
    from gateway import session_context
    monkeypatch.setattr(session_context, 'get_session_env', lambda _: session_id)
    from cpipe.delivery import approach
    monkeypatch.setattr(approach, 'roles', lambda home: (None, {'forge': 'm1', 'sentry': 'm2'}))
    ctx = Context()
    queued = []
    setattr(ctx, 'inject_message', lambda text, **kw: queued.append(text) or injection)
    native.register(ctx)
    out = ctx.commands['cp-run']('Investigate a bug')
    plain(out)
    for prompt in queued:
        plain(prompt)
    if injection:  # a new terminal chat has no session yet; its first turn binds the receipt
        assert out == 'cpipe: /cp-run queued in this session.'
    else:
        assert 'no work started' in out


def test_invalid_intent_response_is_plain():
    out = native.execute({})
    plain(out)
    assert 'error' in json.loads(out)


def test_live_probe_says_it_needs_pytest_when_hermes_python_lacks_it(monkeypatch, capsys):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name, *a: None if name == 'pytest' else real(name, *a))
    assert main(['live-probe', '--builder', 'forge', '--reviewer', 'sentry']) == 1
    assert 'needs pytest' in capsys.readouterr().err
