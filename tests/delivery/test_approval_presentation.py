"""Approval copy describes actual authority, not inferred shipping permission."""
from dataclasses import replace

import pytest

from cpipe.delivery.approval import presentation
from .test_execution_plan import plan


@pytest.mark.parametrize('capabilities', [frozenset({'local'}), frozenset({'local', 'push'}),
    frozenset({'local', 'push', 'draft_pr', 'mark_ready'}), frozenset()])
def test_presentation_names_each_granted_capability_without_inventing_authority(capabilities):
    original = plan()
    frozen = replace(original, envelope=replace(original.envelope, capabilities=capabilities))
    title, description = presentation(frozen, 'revision-fixture')
    assert title == frozen.goal
    labels = {'local': 'Local development work', 'push': 'Push the branch',
              'draft_pr': 'Open a draft pull request', 'mark_ready': 'Mark the pull request ready for review'}
    for capability, label in labels.items():
        assert (label in description) is (capability in capabilities)
    if 'push' not in capabilities:
        assert 'No pushes.' in description
    if not {'draft_pr', 'mark_ready'} & capabilities:
        assert 'No pull-request publication or status changes.' in description
    assert 'No merging, deployment or credential changes.' in description
    assert 'not sandboxed' in description
    assert 'revision-fixture' in description
    assert '"schema"' not in description
    assert 'Approving does not start workers' in description
    for check in frozen.checks:
        assert check.obligation in description
    for path in frozen.forbidden_paths:
        assert path in description
