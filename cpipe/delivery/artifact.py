"""Conservative Git-visible artifact identity for revision-bound observations.

Includes HEAD, branch, staged entries, final tracked/untracked content and modes.
Ignored build outputs are not source inputs; check definitions must separately
bind external fixtures/environment. Symlinks, gitlinks, unmerged indexes and
racy reads are unavailable, never inferred green. This is not a sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import subprocess

from .contracts import relative_path
from .journal import encoded


@dataclass(frozen=True)
class Artifact:
    digest: str
    head: str
    branch: str
    common_git_dir: str
    files: tuple[tuple[str, str, int], ...]


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env['GIT_TERMINAL_PROMPT'] = '0'
    return subprocess.run(['git', '-c', 'core.fsmonitor=false', *args], cwd=root,
        env=env, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=20).stdout


def _file(root, name):
    relative_path(name)
    path = root / name
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('symlink artifact is unsupported')
    try:
        before = path.lstat()
    except FileNotFoundError:
        return name, 'missing', 0
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('artifact must contain regular files')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError('artifact must contain regular files')
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
        after = os.fstat(stream.fileno())
    def signature(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode
    if signature(before) != signature(opened) or signature(opened) != signature(after):
        raise ValueError('artifact changed during read')
    try:
        if signature(after) != signature(path.lstat()):
            raise ValueError('artifact replaced during read')
    except FileNotFoundError as exc:
        raise ValueError('artifact deleted during read') from exc
    return name, checksum.hexdigest(), stat.S_IMODE(after.st_mode)


def _scan(root):
    top = Path(os.fsdecode(git(root, 'rev-parse', '--show-toplevel')).strip()).resolve(strict=True)
    if top != root:
        raise ValueError('artifact root must be the exact Git worktree')
    head = git(root, 'rev-parse', '--verify', 'HEAD').decode().strip()
    branch = git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD').decode().strip()
    common = os.fsdecode(git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')).strip()
    index = git(root, 'ls-files', '--stage', '-z')
    for entry in index.split(b'\0'):
        if not entry:
            continue
        header, _ = entry.split(b'\t', 1)
        mode, _, stage = header.split()
        if stage != b'0' or mode in {b'120000', b'160000'}:
            raise ValueError('unmerged, symlink or submodule artifact is unsupported')
    names = git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z')
    files = tuple(_file(root, os.fsdecode(name)) for name in sorted(set(names.split(b'\0')) - {b''}))
    payload = encoded([head, branch, common, hashlib.sha256(index).hexdigest(), files])
    return Artifact(hashlib.sha256(payload.encode()).hexdigest(), head, branch, common, files)


def fingerprint(worktree):
    root = Path(worktree).absolute()
    if root.resolve(strict=True) != root:
        raise ValueError('symlink worktree is unsupported')
    first = _scan(root)
    if _scan(root) != first:
        raise ValueError('artifact changed during fingerprint')
    return first


def validate_worktree(plan):
    value = fingerprint(plan.worktree)
    if value.branch != plan.envelope.branch:
        raise ValueError('execution branch differs from approved plan')
    if Path(value.common_git_dir).resolve(strict=True) != Path(plan.common_git_dir).resolve(strict=True):
        raise ValueError('common Git directory differs from approved plan')
    try:
        git(plan.worktree, 'merge-base', '--is-ancestor', plan.base_commit, value.head)
    except subprocess.CalledProcessError as exc:
        raise ValueError('approved base is not an ancestor of current artifact') from exc
    return value
