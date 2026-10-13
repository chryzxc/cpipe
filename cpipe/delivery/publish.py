"""Draft pull request for a delivery the user asked one for: after its checks and review pass,
the builder writes the commits, title and description the user's conventions ask for, each commit is pushed
on its own to the delivery's branch, gh pr create --draft opens the PR, and the reviewer verifies it.
Never merges, never marks ready, never pushes another branch.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile


def _run(cwd, *argv, stdin=None, env=None):
    extra, env = env or {}, {k: v for k, v in os.environ.items()  # the user's commit identity stays
                             if not k.startswith('GIT_') or k.startswith(('GIT_AUTHOR_', 'GIT_COMMITTER_'))}
    env.update(GIT_TERMINAL_PROMPT='0', GH_PROMPT_DISABLED='1', GH_NO_UPDATE_NOTIFIER='1', **extra)
    return subprocess.run(argv, cwd=cwd, env=env, input=stdin, capture_output=True, text=True,
                          check=True, timeout=120).stdout.strip()


def _git(cwd, *args, stdin=None, env=None):
    return _run(cwd, 'git', '-c', 'core.hooksPath=/dev/null', *args, stdin=stdin, env=env)


def github_repo(root):
    """owner/name of origin on GitHub."""
    try:
        url = _git(root, 'config', '--get', 'remote.origin.url')  # as configured, before URL rewrites
    except subprocess.CalledProcessError:
        raise ValueError(f'a draft PR needs an origin remote and {root} has none: ask the user which GitHub repository '
                         f'URL to push to; with their answer run git -C {root} remote add origin <url> and call '
                         'again') from None
    match = re.fullmatch(r'(?:https://|ssh://git@|git@)github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?', url)
    if not match:
        raise ValueError(f'a draft PR needs origin to be a GitHub repository, and origin of {root} is not: ask the '
                         f'user which GitHub repository URL to push to; with their answer run git -C {root} '
                         'remote set-url origin <url> and call again')
    return match.group(1)


def remote_base(root, base):
    """The origin branch the PR targets: base, or base without its origin/ prefix."""
    name = base.removeprefix('origin/')
    try:
        _git(root, 'rev-parse', '--verify', '-q', f'refs/remotes/origin/{name}^{{commit}}')
    except subprocess.CalledProcessError:
        raise ValueError('a draft PR needs base to be a branch on origin; fetch it or choose one') from None
    return name


def preflight(root, base, start, branch):
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
    try:
        _git(root, 'check-ref-format', '--branch', branch)
    except subprocess.CalledProcessError:
        raise ValueError('branch is not a valid branch name') from None
    taken = subprocess.run(['git', 'rev-parse', '--verify', '-q', f'refs/heads/{branch}'], cwd=root,
                           capture_output=True, timeout=120).returncode == 0
    try:
        taken = taken or _git(root, 'ls-remote', '--heads', 'origin', f'refs/heads/{branch}')
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ValueError('could not reach origin to check that the branch name is new') from None
    if taken:  # the PR push is forced: never onto a branch someone else has
        raise ValueError('branch already exists; choose a new branch name')


def trees(root, base, groups):
    """One tree per commit: each adds its group of changed paths, as they are in the worktree, to the last."""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(GIT_INDEX_FILE=os.path.join(tmp, 'index'))
        _git(root, 'read-tree', base, env=env)
        out = []
        for paths in groups:
            _git(root, 'update-index', '--add', '--remove', '-z', '--stdin', env=env, stdin='\0'.join(paths) + '\0')
            out.append(_git(root, 'write-tree', env=env))
        return out


def open_draft(plan, commits, title, body):
    """Push each (tree, message) as one new commit on the delivery's branch, one push per commit, and open or
    update its draft PR with title and body. Returns (url, commit). Safe to repeat: the branch is the delivery's own."""
    root, branch = plan.worktree, plan.envelope.branch
    repo, base = github_repo(root), remote_base(root, plan.envelope.base)
    fields = 'url,isDraft,headRefOid,baseRefName,headRefName,state'
    commit = plan.base_commit  # a repeat replaces the earlier commits
    for n, (tree, message) in enumerate(commits):
        commit = _git(root, 'commit-tree', '--no-gpg-sign', tree, '-p', commit, '-F', '-', stdin=message)
        _git(root, 'push', '--force', '--no-verify', 'origin', f'{commit}:refs/heads/{branch}')
        if n == 0 and not json.loads(_run(root, 'gh', 'pr', 'list', '--repo', repo, '--head', branch, '--state',
                                          'open', '--json', fields, '--limit', '1') or '[]'):
            _run(root, 'gh', 'pr', 'create', '--repo', repo, '--draft', '--base', base, '--head', branch,
                 '--title', title, '--body-file', '-', stdin=body)
    _run(root, 'gh', 'pr', 'edit', branch, '--repo', repo, '--title', title, '--body-file', '-', stdin=body)
    pr = json.loads(_run(root, 'gh', 'pr', 'view', branch, '--repo', repo, '--json', fields))
    if (pr.get('state') != 'OPEN' or not pr.get('isDraft') or pr.get('headRefOid') != commit
            or pr.get('baseRefName') != base or pr.get('headRefName') != branch):
        raise ValueError('the pull request on GitHub does not match the pushed draft')
    return pr['url'], commit


def readback(plan):
    """The published pull request as GitHub shows it, for the verifier."""
    root, repo = plan.worktree, github_repo(plan.worktree)
    pr = json.loads(_run(root, 'gh', 'pr', 'view', plan.envelope.branch, '--repo', repo, '--json',
                         'title,body,headRefName,baseRefName,isDraft,commits'))
    pr['commits'] = [dict(message=(c.get('messageHeadline', '') + '\n\n' + c.get('messageBody', '')).strip(),
                          files=_git(root, 'diff-tree', '--no-commit-id', '--name-only', '-r', c['oid']).splitlines())
                     for c in pr.get('commits', ())]
    return pr
