import json

from cpipe import register


class RecordingContext:
    def __init__(self):
        self.tools = []
        self.cli_commands = []
        self.hooks = []

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools.append(name)

    def register_cli_command(self, name, help=None, setup_fn=None, handler_fn=None,
                             description=None, **kwargs):
        self.cli_commands.append(name)

    def register_hook(self, hook_name, callback, **kwargs):
        self.hooks.append((hook_name, callback))


def test_registers_delivery_tools():
    context = RecordingContext()

    register(context)

    assert sorted(context.tools) == [
        'delivery_board_intelligence',
        'delivery_check_policy',
        'delivery_mutation_check',
        'delivery_status',
        'delivery_submit',
        'delivery_test_env',
        'delivery_verify_failed',
        'delivery_watch',
    ]


def test_registers_doctor_cli_and_session_hook():
    context = RecordingContext()

    register(context)

    assert context.cli_commands == ['cpipe']
    assert [h[0] for h in context.hooks] == ['on_session_end', 'on_session_end', 'on_kanban_dispatch_tick', 'on_kanban_dispatch_tick', 'pre_llm_call', 'pre_llm_call', 'pre_llm_call', 'transform_tool_result', 'post_llm_call', 'pre_approval_request', 'pre_tool_call', 'pre_tool_call', 'pre_tool_call']


def test_mutation_tool_schema_requires_worktree():
    from cpipe import _MUTATION_SCHEMA

    required = _MUTATION_SCHEMA['function']['parameters']['required']
    assert required == ['worktree', 'file_path', 'test_filter']


def test_session_end_hook_appends_jsonl(tmp_path, monkeypatch):
    import cpipe as plugin

    monkeypatch.setattr(plugin, '_METRICS_LOG', tmp_path / 'metrics.jsonl')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    plugin._on_session_end(profile='forge', session_id='s1', duration=None)

    lines = (tmp_path / 'metrics.jsonl').read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record['profile'] == 'forge'
    assert 'duration' not in record


def test_a_registered_hook_runs_the_modules_current_code(monkeypatch):
    """A fix to a module reaches Herm's long-running chat server without a relaunch."""
    import cpipe
    from cpipe import status
    context = RecordingContext()
    register(context)
    compact = next(cb for name, cb in context.hooks if name == "transform_tool_result")
    monkeypatch.setattr(status, "compact_show", lambda *a, **kw: "new code")
    assert compact() == "new code"

    reloaded = []
    monkeypatch.setattr(cpipe.importlib, "reload", lambda mod: reloaded.append(mod.__name__))
    monkeypatch.setattr(cpipe, "_loaded_at", 0)
    compact()
    assert reloaded[0] == "cpipe.home" and "cpipe.submit" in reloaded
    reloaded.clear()
    compact()
    assert reloaded == []
