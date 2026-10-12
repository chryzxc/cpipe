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


def test_worktrees_get_cloned_dependencies_only_when_safe(repo, monkeypatch):
    import subprocess, sys
    from cpipe.delivery import controller
    (repo / '.gitignore').write_text('__pycache__/\n*.pyc\nnode_modules/\n.venv/\nvendor/\n')
    (repo / 'package-lock.json').write_text('{"v": 1}')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'deps')
    (repo / 'node_modules' / 'left').mkdir(parents=True)
    (repo / 'node_modules' / 'left' / 'index.js').write_text('x')
    (repo / '.venv').mkdir()
    (repo / '.venv' / 'pyvenv.cfg').write_text('home = /usr/bin')
    (repo / 'vendor' / 'lib').mkdir(parents=True)
    (repo / 'vendor' / 'lib' / '.git').mkdir()
    # Plain copies stand in for copy-on-write clones on file systems that cannot clone.
    real = subprocess.run
    monkeypatch.setattr(controller.subprocess, 'run', lambda argv, **kw: real(
        ['cp', '-R', *argv[-2:]] if argv[0] == 'cp' else argv, **kw))
    head = git(repo, 'rev-parse', 'HEAD').strip()
    path = controller.isolate(repo, head, 'd1')
    assert (path / 'node_modules' / 'left' / 'index.js').read_text() == 'x'
    assert not (path / '.venv').exists() and not (path / 'vendor').exists()  # virtualenvs, nested repos
    (path / 'node_modules' / 'left' / 'index.js').write_text('changed')
    assert (repo / 'node_modules' / 'left' / 'index.js').read_text() == 'x'  # never linked back
    (repo / 'package-lock.json').write_text('{"v": 2}')  # installed for other versions: nothing copied
    assert not (controller.isolate(repo, head, 'd2') / 'node_modules').exists()


def test_pull_request_content_is_checked_before_anything_is_pushed(repo):
    from pathlib import Path
    from types import SimpleNamespace
    import pytest
    from cpipe.delivery.controller import pull_request
    from cpipe.delivery.publish import trees
    plan = SimpleNamespace(envelope=SimpleNamespace(delivery_id='cp-123'), worktree=str(repo))
    ok = dict(title='Fix doubling', body='Why and how.', commits=[dict(message='test: doubling', paths=['t.py']),
                                                                  dict(message='fix: doubling', paths=['double.py'])])
    assert [m for _, m in pull_request(plan, ok, ['double.py', 't.py'])[0]] == ['test: doubling\n', 'fix: doubling\n']
    for bad, why in ((dict(ok, commits=ok['commits'][:1]), 'every changed path'),
                     (dict(ok, body='See ' + str(Path.home()) + '/x'), 'internal'),
                     (dict(ok, title='cp-123 fix'), 'internal'),
                     (dict(ok, title='two\nlines'), 'one line')):
        with pytest.raises(ValueError, match=why):
            pull_request(plan, bad, ['double.py', 't.py'])
    (repo / 't.py').write_text('t\n')
    (repo / 'double.py').write_text('def double(n): return n * 2\n')
    base = git(repo, 'rev-parse', 'HEAD').strip()
    first, second = trees(repo, base, [['t.py'], ['double.py']])
    assert git(repo, 'ls-tree', '--name-only', first).split() == ['.gitignore', 'double.py', 't.py']
    assert git(repo, 'show', f'{first}:double.py') == git(repo, 'show', f'{base}:double.py')  # only t.py so far
    assert git(repo, 'show', f'{second}:double.py') == 'def double(n): return n * 2'
