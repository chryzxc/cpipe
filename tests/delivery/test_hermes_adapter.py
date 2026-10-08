"""Installed-engine conformance; no model requests or live operator state."""
from cpipe.engines import hermes


def test_ordinary_execution_preserves_approvals_and_profile_configuration(tmp_path):
    argv = hermes.command({'profile': 'custom'}, 'task', tmp_path, None)
    assert argv[argv.index('-p') + 1] == 'custom'
    assert argv[argv.index('--in') + 1] == str(tmp_path)
    assert '--pass-session-id' in argv
    for forbidden in ('--yolo', '--toolsets', '--ignore-rules', '--model'):
        assert forbidden not in argv


def test_discovery_remains_tool_free_and_cannot_resume_builder(tmp_path):
    argv = hermes.command({'profile': 'custom', 'discovery_only': True},
                         'evidence', tmp_path, 'builder-session')
    assert argv[argv.index('--toolsets') + 1] == 'none'
    assert argv[argv.index('--max-turns') + 1] == '1'
    assert '--resume' not in argv
    assert '--yolo' not in argv
