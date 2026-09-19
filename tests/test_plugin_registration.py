import json

from software_delivery import register


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


def test_registers_three_delivery_tools():
    context = RecordingContext()

    register(context)

    assert sorted(context.tools) == [
        'delivery_board_intelligence',
        'delivery_check_policy',
        'delivery_mutation_check',
    ]


def test_registers_doctor_cli_and_session_hook():
    context = RecordingContext()

    register(context)

    assert context.cli_commands == ['software-delivery']
    assert [h[0] for h in context.hooks] == ['on_session_end']


def test_mutation_tool_schema_requires_worktree():
    from software_delivery import _MUTATION_SCHEMA

    required = _MUTATION_SCHEMA['function']['parameters']['required']
    assert required == ['worktree', 'file_path', 'test_filter']


def test_session_end_hook_appends_jsonl(tmp_path, monkeypatch):
    import software_delivery as plugin

    monkeypatch.setattr(plugin, '_METRICS_LOG', tmp_path / 'metrics.jsonl')
    plugin._on_session_end(profile='forge', session_id='s1', duration=None)

    lines = (tmp_path / 'metrics.jsonl').read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record['profile'] == 'forge'
    assert 'duration' not in record
