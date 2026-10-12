"""Artifact identity covers final Git-visible content, not HEAD alone."""
import os
from pathlib import Path
import subprocess

import pytest


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-b', 'feature')
    (root / '.gitignore').write_text('__pycache__/\n*.pyc\n')
    (root / 'double.py').write_text('def double(n): return n\n')
    git(root, 'add', '.gitignore', 'double.py')
    git(root, '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null',
        'commit', '-m', 'test: seed artifact fixture')
    return root


def test_artifact_changes_for_dirty_tracked_untracked_deleted_and_mode_content(repo):
    from cpipe.delivery.artifact import fingerprint
    first = fingerprint(repo)
    assert first == fingerprint(repo)
    assert len(first.digest) == 64
    assert first.head == git(repo, 'rev-parse', 'HEAD')
    (repo / 'double.py').write_text('def double(n): return n * 2\n')
    dirty = fingerprint(repo)
    assert first.digest != dirty.digest and first.head == dirty.head
    (repo / 'new.txt').write_text('new content')
    added = fingerprint(repo)
    assert added.digest != dirty.digest
    (repo / 'new.txt').chmod(0o755)
    mode = fingerprint(repo)
    assert mode.digest != added.digest
    (repo / 'double.py').unlink()
    assert fingerprint(repo).digest != mode.digest


def test_ignored_generated_files_do_not_invalidate_source_artifact(repo):
    from cpipe.delivery.artifact import fingerprint
    first = fingerprint(repo)
    cache = repo / '__pycache__'
    cache.mkdir()
    (cache / 'generated.pyc').write_bytes(b'generated')
    assert fingerprint(repo) == first


def test_staged_content_is_bound_even_with_same_final_worktree(repo):
    from cpipe.delivery.artifact import fingerprint
    (repo / 'double.py').write_text('def double(n): return n * 2\n')
    dirty = fingerprint(repo)
    git(repo, 'add', 'double.py')
    assert fingerprint(repo).digest != dirty.digest


def test_symlinks_and_nested_repositories_are_bound_without_being_read(repo, tmp_path):
    from cpipe.delivery.artifact import fingerprint, _file
    (tmp_path / 'outside').write_text('private')
    (repo / 'link').symlink_to(tmp_path / 'outside')
    git(repo, 'worktree', 'add', '-q', '.worktrees/other', '-b', 'other')  # another agent's worktree inside
    first = fingerprint(repo)
    files = {name: (digest, mode) for name, digest, mode in first.files}
    assert files['link'][0].startswith('symlink:') and files['.worktrees/other'] == ('repository', 0o160000)
    (tmp_path / 'outside').write_text('changed')  # the target is not read
    (repo / '.worktrees/other/double.py').write_text('changed by someone else')
    assert fingerprint(repo) == first
    (repo / 'link').unlink()
    (repo / 'link').symlink_to(tmp_path / 'elsewhere')
    assert fingerprint(repo).digest != first.digest
    git(repo, 'add', 'link')  # a staged symlink is bound too
    assert fingerprint(repo).digest != first.digest
    os.mkfifo(repo / 'pipe')
    # Git does not list untracked FIFOs; an explicitly supplied file input still
    # fails closed before opening it and cannot block on a pipe.
    with pytest.raises(ValueError, match='regular'):
        _file(repo, 'pipe')


def test_worktree_identity_rejects_wrong_branch_and_common_directory(repo):
    from cpipe.delivery.artifact import validate_worktree
    from .test_execution_plan import plan
    p = plan(worktree=str(repo), common_git_dir=str(repo / '.git'),
             base_commit=git(repo, 'rev-parse', 'HEAD'))
    assert validate_worktree(p).head == p.base_commit
    from dataclasses import replace
    with pytest.raises(ValueError, match='branch'):
        validate_worktree(replace(p, envelope=replace(p.envelope, branch='wrong')))
    with pytest.raises(ValueError, match='common'):
        validate_worktree(replace(p, common_git_dir=str(repo.parent)))


def test_delivery_worktrees_sit_next_to_the_main_repository(repo):
    from cpipe.delivery.controller import isolate
    git(repo, 'worktree', 'add', '-q', str(repo.parent / 'linked'), '-b', 'linked')
    head = git(repo, 'rev-parse', 'HEAD').strip()
    for source, name in ((repo, 'd1'), (repo.parent / 'linked', 'd2')):  # a linked checkout still lands by the repo
        assert isolate(source, head, name) == (repo.parent / (repo.name + '-worktrees') / name).resolve()
