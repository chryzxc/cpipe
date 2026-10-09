"""Public command names, help and responses use plain cpipe terminology."""
import argparse
import json

import pytest

from cpipe import native
from cpipe.runtime.cli import main, setup_parser
from .test_v2_entrypoints import Context


def plain(text):
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


@pytest.mark.parametrize('session_id,injection', [('', True), ('s1', False), ('s1', True)])
def test_run_responses_and_injected_prompt_are_plain(hermes_home, monkeypatch, session_id, injection):
    from gateway import session_context
    monkeypatch.setattr(session_context, 'get_session_env', lambda _: session_id)
    ctx = Context()
    queued = []
    setattr(ctx, 'inject_message', lambda text, **kw: queued.append(text) or injection)
    native.register(ctx)
    out = ctx.commands['cp-run']('Investigate a bug')
    plain(out)
    for prompt in queued:
        plain(prompt)
    if session_id and injection:
        assert out == 'cpipe: /cp-run queued in this session.'
    else:
        assert 'no work started' in out


def test_invalid_intent_response_is_plain():
    out = native.execute({})
    plain(out)
    assert 'error' in json.loads(out)
