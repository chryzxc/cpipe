"""Draft pull request for a delivery the user asked one for: after its checks and review pass,
one commit of the worktree, a push of the delivery's own branch to origin, and gh pr create --draft.
Never merges, never marks ready, never pushes another branch.
"""
import json
import os
import re
import shutil
import subprocess


def _run(cwd, *argv, stdin=None):
    env = {k: v for k, v in os.environ.items()  # the user's commit identity stays
           if not k.startswith('GIT_') or k.startswith(('GIT_AUTHOR_', 'GIT_COMMITTER_'))}
    env.update(GIT_TERMINAL_PROMPT='0', GH_PROMPT_DISABLED='1', GH_NO_UPDATE_NOTIFIER='1')
    return subprocess.run(argv, cwd=cwd, env=env, input=stdin, capture_output=True, text=True,
                          check=True, timeout=120).stdout.strip()


def _git(cwd, *args, stdin=None):
    return _run(cwd, 'git', '-c', 'core.hooksPath=/dev/null', *args, stdin=stdin)


def github_repo(root):
    """owner/name of origin on GitHub."""
    try:
        url = _git(root, 'remote', 'get-url', 'origin')
    except subprocess.CalledProcessError:
        raise ValueError('a draft PR needs an origin remote') from None
    match = re.fullmatch(r'(?:https://|ssh://git@|git@)github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?', url)
    if not match:
        raise ValueError('a draft PR needs origin to be a GitHub repository')
    return match.group(1)


def remote_base(root, base):
    """The origin branch the PR targets: base, or base without its origin/ prefix."""
    name = base.removeprefix('origin/')
    try:
        _git(root, 'rev-parse', '--verify', '-q', f'refs/remotes/origin/{name}^{{commit}}')
    except subprocess.CalledProcessError:
        raise ValueError('a draft PR needs base to be a branch on origin; fetch it or choose one') from None
    return name


def preflight(root, base, start):
    """Refuse at planning, not after the work is done, when the PR could not be opened."""
    github_repo(root)
    name = remote_base(root, base)
    if not shutil.which('gh'):
        raise ValueError('a draft PR needs the GitHub CLI (gh) on PATH')
    try:
        _run(root, 'gh', 'auth', 'status')
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ValueError('gh is not logged in; run gh auth login first') from None
    try:
        _git(root, 'merge-base', '--is-ancestor', start, f'refs/remotes/origin/{name}')
    except subprocess.CalledProcessError:
        raise ValueError('base has commits that are not on origin; push them first so the PR shows '
                         'only this change') from None


def open_draft(plan, tree, message, body):
    """Commit tree on the worktree's HEAD, push it as the delivery branch, open or reuse its draft PR.
    Returns (url, commit). Safe to repeat: the branch is the delivery's own."""
    root, branch = plan.worktree, plan.envelope.branch
    repo, base = github_repo(root), remote_base(root, plan.envelope.base)
    commit = _git(root, 'commit-tree', '--no-gpg-sign', tree, '-p', 'HEAD', '-F', '-', stdin=message)
    _git(root, 'push', '--force', '--no-verify', 'origin', f'{commit}:refs/heads/{branch}')
    fields = 'url,isDraft,headRefOid,baseRefName,headRefName,state'
    found = json.loads(_run(root, 'gh', 'pr', 'list', '--repo', repo, '--head', branch, '--state', 'open',
                            '--json', fields, '--limit', '1') or '[]')
    if not found:
        _run(root, 'gh', 'pr', 'create', '--repo', repo, '--draft', '--base', base, '--head', branch,
             '--title', message.split('\n', 1)[0], '--body-file', '-', stdin=body)
    pr = json.loads(_run(root, 'gh', 'pr', 'view', branch, '--repo', repo, '--json', fields))
    if (pr.get('state') != 'OPEN' or not pr.get('isDraft') or pr.get('headRefOid') != commit
            or pr.get('baseRefName') != base or pr.get('headRefName') != branch):
        raise ValueError('the pull request on GitHub does not match the pushed draft')
    return pr['url'], commit
