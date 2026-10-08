"""Installed-host characterization, not a claim of a delivery sandbox.

No provider requests, operator profile writes, or external actions. The positive
control writes only a harmless marker under the test's disposable scratch tree.
"""
import json
import sys
from pathlib import Path
import shlex

import pytest


@pytest.mark.parametrize('choice', ['deny', 'once'])
def test_discovered_guard_uses_native_approval_before_marker_write(
        hermes_home, tmp_path, monkeypatch, choice):
    import model_tools
    from hermes_cli import plugins
    from tools import approval
    from tools.approval_context import set_current_session_key, reset_current_session_key

    name = 'delivery-boundary-probe'
    plugin = hermes_home / 'plugins' / name
    plugin.mkdir(parents=True)
    (plugin / 'plugin.yaml').write_text(
        'name: delivery-boundary-probe\nversion: 0.1.0\n'
        'description: Disposable approval conformance probe\n'
        'provides_tools: [delivery_boundary_probe]\n'
        'provides_hooks: [pre_tool_call]\n')
    (plugin / '__init__.py').write_text('''
import json
from pathlib import Path

def guard(tool_name, **kwargs):
    if tool_name == 'delivery_boundary_probe':
        return {'action': 'approve', 'message': 'Disposable marker write',
                'rule_key': 'delivery-boundary-probe'}

def write_marker(args, **kwargs):
    Path(args['path']).write_text('observed')
    return json.dumps({'written': True})

def register(ctx):
    ctx.register_hook('pre_tool_call', guard)
    ctx.register_tool(name='delivery_boundary_probe', toolset='delivery_boundary_probe',
        schema={'name': 'delivery_boundary_probe', 'description': 'Write a disposable marker',
                'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}},
                               'required': ['path'], 'additionalProperties': False}},
        handler=write_marker)
''')
    (hermes_home / 'config.yaml').write_text(
        'plugins:\n  enabled: [delivery-boundary-probe]\n'
        'approvals:\n  mode: manual\n')
    monkeypatch.delenv('HERMES_YOLO_MODE', raising=False)
    plugins.discover_plugins(force=True)
    marker = tmp_path / 'native-marker.txt'
    seen = []

    def notify(payload):
        seen.append(payload)
        assert approval.resolve_gateway_approval(
            'boundary-route', choice, request_id=payload['request_id'])

    approval.register_gateway_notify('boundary-route', notify)
    token = set_current_session_key('boundary-route')
    try:
        result = json.loads(model_tools.handle_function_call(
            'delivery_boundary_probe', {'path': str(marker)}, session_id='boundary-session'))
        assert len(seen) == 1
        if choice == 'deny':
            assert 'error' in result
            assert not marker.exists()
        else:
            assert result['written'] is True
            assert marker.read_text() == 'observed'
    finally:
        approval.unregister_gateway_notify('boundary-route')
        reset_current_session_key(token)
        plugins.get_plugin_manager().unload()


def test_local_command_approval_is_not_a_write_scope_sandbox(hermes_home, tmp_path, monkeypatch):
    from tools.approval import check_all_command_guards
    (hermes_home / 'config.yaml').write_text('approvals:\n  mode: manual\n')
    monkeypatch.delenv('HERMES_YOLO_MODE', raising=False)
    worktree = tmp_path / 'worktree'
    worktree.mkdir()
    outside = tmp_path / 'outside.txt'
    command = f'printf probe > {shlex.quote(str(outside))}'
    # Guard decision only: do not execute the command. Unlike Python -c, an
    # ordinary redirect to a nonsensitive path is not a dangerous-command
    # pattern. It is still outside the worktree: approval is not confinement.
    decision = check_all_command_guards(command, 'local')
    assert decision['approved'] is True
    assert not outside.exists()


def test_native_file_tool_can_write_outside_worktree_in_disposable_tree(tmp_path, monkeypatch):
    import model_tools
    from hermes_cli import plugins
    monkeypatch.setenv('TERMINAL_ENV', 'local')
    worktree = tmp_path / 'worktree'
    worktree.mkdir()
    monkeypatch.setenv('TERMINAL_CWD', str(worktree))
    monkeypatch.chdir(worktree)
    plugins.discover_plugins(force=True)
    outside = tmp_path / 'outside.txt'
    result = json.loads(model_tools.handle_function_call(
        'write_file', {'path': str(outside), 'content': 'disposable boundary probe'},
        session_id='boundary-session'))
    assert 'error' not in result, result
    assert outside.read_text() == 'disposable boundary probe'
