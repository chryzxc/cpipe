"""Production local controller, sharing the receipt-aware native adapters.

Trusted local workers are not sandboxed. A draft PR is opened only when the approved plan grants it.
"""
from contextlib import closing
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from .artifact import validate_worktree
from .check_runner import CommandCheck, baseline_path, take_baseline
from .contracts import Envelope, Budget, UNAVAILABLE_REASONS
from .schema import ExecutionPlan
from .service import Service, Event, deadline, resumable  # noqa: F401 (resumable: used by native)
from .native_bridge import NativeBridge
from .hermes_worker import (at_base, changed_since_base, reattach, release, HermesWorker,
                            keep, roll_back, stop_orphan)
from .workers import observe_implementation, usage, WorkerRefused
from .review import observe_review, review_identity
from .approval import arguments
from .journal import encoded
from ..runtime import hermes_api as h

# Time a frozen plan may wait for consent; the execution budget starts at approval.
APPROVAL_WINDOW = 3600


# Builder/reviewer turns per delivery: room for test, fix and review rounds plus self-repair.
CALLS = 40

# A failing worker is resumed with the reason, then retried in a fresh session; on the
# next failure the next approved actor takes over. Retries back off for provider hiccups.
FAILURES = 3

# Times a run that is still within its calls renews its own time budget before it stops and tells the user.
AUTO_RENEWALS = 2
ACTIVE_STEPS = frozenset({'tests', 'checks', 'write_tests', 'implement', 'review', 'publish'})
BACKOFF = 15
WORKER_STEPS = frozenset({'write_tests', 'implement', 'review'})


STEP_LINES = {'write_tests': '{who} is writing the failing test', 'tests': 'running the new test (it must fail first)',
              'implement': '{who} is implementing', 'checks': 'running the checks', 'review': '{who} is reviewing',
              'publish': 'opening the draft pull request'}
DRAFT = frozenset({'local', 'push', 'draft_pr'})


def tell(session_id, delivery_id, text, done=False):
    """One display-only line for the originating chat in the desktop app: no model turn, never fatal."""
    if 'tui_gateway.server' not in sys.modules:
        return  # no app attached here; importing it would take over this process's stdout
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event
        broadcast_plugin_event('cpipe', 'progress', dict(session_id=session_id, delivery_id=delivery_id,
                                                         text=text, done=done))
    except Exception:
        pass


def inside(name, paths):
    return any(name == p or name.startswith(p + '/') for p in paths)


def settle(state, store, service):
    """Before each step: remember a verified worktree, roll a failed step's leftovers back, or adopt it."""
    try:
        clean = validate_worktree(state.plan).digest == state.artifact
    except (ValueError, OSError):
        clean = False
    if clean:
        keep(state.plan, store)
        return state
    try:
        if roll_back(state.plan, store) and validate_worktree(state.plan).digest == state.artifact:
            return state
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    # Nothing verified to return to (or it is gone): continue from the worktree as it is, unless it
    # touches a forbidden path. The checks prove it again before any review.
    reattach(state.plan)
    plan = state.plan
    if any(inside(n, plan.forbidden_paths) for n in changed_since_base(plan)):
        raise ValueError('the worktree has changes in a forbidden path and could not be restored')
    digest = validate_worktree(plan).digest
    bridge = NativeBridge(service, None, session_id=plan.envelope.session_id, profile_id=plan.envelope.profile_id)
    return service.accept(bridge._event(state, 'worktree_adopted', result_artifact=digest))[0]


def summary(state, service=None):
    result = dict(delivery_id=state.plan.envelope.delivery_id,
        phase=state.phase, activity=state.activity, artifact=state.artifact,
        approved=state.approved, review_cycles=state.review_cycles,
        outcomes=[r.outcome.value for r in state.receipts],
        completion_target=state.plan.completion_target,
        checks={r.obligation: r.outcome.value + (': ' + UNAVAILABLE_REASONS[r.reason] if r.reason else '')
                for r in state.receipts})
    from .findings import blocks
    from .review import NOTES
    from .workers import DISPUTES
    found = [r.finding for r in state.findings if r.finding.disposition == 'open']
    disputed = {f.id: DISPUTES.get(state.plan.envelope.delivery_id + ':' + f.id) for f in found}
    if found:  # unresolved blocking findings first, then advice the run did not act on
        result['findings'] = [dict(id=f.id, severity=f.severity, blocking=blocks(f), in_scope=f.in_scope,
                                   text=NOTES.get(f.root_cause, '(text not kept after a restart)'),
                                   **({'builder_disputes': disputed[f.id]} if disputed[f.id] else {}))
                              for f in sorted(found, key=lambda f: not blocks(f))]
    if state.phase == 'proposal' and not state.approved:
        result['approval_arguments'] = arguments(state)
    if service is not None:
        result['tokens'] = usage(service, state.plan.envelope.delivery_id)
        row = service.journal.db.execute('SELECT payload FROM intent WHERE id=?',
                                         ('pr:' + state.plan.envelope.delivery_id,)).fetchone()
        if row:
            result['pull_request'] = json.loads(row[0])['url']
    return result


def propose(service, plan, checks):
    if (plan.envelope.capabilities != (DRAFT if plan.completion_target == 'draft' else frozenset({'local'}))
            or plan.interactive or plan.repair_allowed or plan.transient_retries
            or any(a.engine != 'hermes' for a in plan.actors)
            or len({a.profile for a in plan.actors}) != len(plan.actors)
            or len(checks) == 0 or any(type(c) is not CommandCheck for c in checks)
            or tuple(c.identity() for c in checks) + (review_identity(),) != plan.checks):
        raise ValueError('cpipe supports explicit Hermes execution with frozen checks and independent review')
    artifact = validate_worktree(plan)
    now = int(time.time())
    e = plan.envelope
    state, _ = service.accept(Event('proposal:' + plan.digest(), 'proposal_created', e.delivery_id,
        plan.digest(), artifact.digest, 0, now, session_id=e.session_id, profile_id=e.profile_id, plan=plan))
    # Specs are frozen alongside the projection; replay may never change them.
    key = 'check-spec:' + plan.digest()
    payload = encoded([dict(obligation=c.obligation, argv=list(c.argv), criteria=list(c.criteria),
                          timeout_seconds=c.timeout_seconds, env=[list(p) for p in c.env]) for c in checks])
    row = service.journal.db.execute('SELECT payload FROM intent WHERE id=?', (key,)).fetchone()
    if row and row[0] != payload:
        raise ValueError('check definition changed')
    service.journal.db.execute('INSERT OR IGNORE INTO intent(id,payload,acknowledged) VALUES (?,?,1)', (key, payload))
    return state


def validate_spec(spec):
    """Preflight a draft without saving a proposal or starting its budget."""
    from .live_probe import observe_actor
    from ..routing import soul
    from .artifact import fingerprint
    expected = {'worktree', 'repo', 'base', 'goal', 'acceptance', 'non_goals', 'write_paths',
                'forbidden_paths', 'builder', 'reviewer', 'seconds', 'checks'}
    if type(spec) is not dict:
        raise ValueError('spec must be an object')
    if set(spec) - {'draft_pr', 'title', 'branch'} != expected:
        # Field names are fixed schema labels, never user values.
        missing, extra = sorted(expected - set(spec)), sorted(set(spec) - expected)
        raise ValueError('spec fields: missing ' + (', '.join(missing) or 'none')
                         + '; unsupported ' + (', '.join(k for k in extra if k.isidentifier()) or 'none'))
    if type(spec.get('draft_pr', False)) is not bool:
        raise ValueError('draft_pr must be true or false')
    if bool(spec.get('draft_pr')) != ('branch' in spec) or 'branch' in spec and (
            type(spec['branch']) is not str or not spec['branch'] or len(spec['branch']) > 200):
        raise ValueError('a draft PR needs branch: a new branch name following the repository\'s and the user\'s '
                         'conventions (and only a draft PR has one)')
    if type(spec['seconds']) is not int or not 30 <= spec['seconds'] <= 7200:
        raise ValueError('cpipe requires a bounded 30..7200 second budget')
    for key in ('acceptance', 'non_goals', 'write_paths', 'forbidden_paths', 'checks'):
        if type(spec[key]) is not list:
            raise ValueError(f'{key} must be a list')
    for key in ('write_paths', 'forbidden_paths'):  # "dir/" is how people write a directory
        spec[key] = [p.rstrip('/') if type(p) is str and p.strip('/') else p for p in spec[key]]

    root = Path(spec['worktree'])
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError('worktree must be an absolute non-symlink directory')
    artifact = fingerprint(root)
    checks = []
    for item in spec['checks']:
        if type(item) is dict and any(type(a) is str and str(root) in a for a in item.get('argv', ())):
            raise ValueError('check commands must not name the investigated checkout: the delivery runs in a new '
                             'worktree of its own, so use commands relative to it that install what they need')
        fields = {'obligation', 'argv', 'timeout_seconds'}
        if type(item) is not dict or not fields <= set(item) <= fields | {'criteria'}:
            raise ValueError('each check needs obligation, argv, timeout_seconds and optional criteria')
        if type(item['argv']) is not list or type(item.get('criteria', [])) is not list:
            raise ValueError('check argv and criteria must be lists')
        checks.append(CommandCheck(item['obligation'], tuple(item['argv']), tuple(item.get('criteria', [])),
                                   item['timeout_seconds']))
    checks = tuple(checks)
    behavior = [c for c in checks if c.obligation == 'behavior']
    if not behavior:
        raise ValueError('one check must have obligation "behavior": the test proving this change, run before implementing')
    if not behavior[0].criteria:
        raise ValueError('the behavior check needs criteria naming the test that proves the change')
    for c in checks:
        c.identity()  # resolves the program now, not after approval
    builders, reviewers = (spec[r] if type(spec[r]) is list else [spec[r]] for r in ('builder', 'reviewer'))
    if (not 1 <= len(builders) <= 3 or not 1 <= len(reviewers) <= 3
            or any(type(name) is not str for name in builders + reviewers)):
        raise ValueError('builder and reviewer are each a profile name or an ordered list of up to 3 '
                         '(the next takes over if one refuses or keeps failing)')
    for name in builders:
        if soul(name)[1]:
            raise ValueError(f'profile {name} is read-only (its SOUL.md forbids edits) and cannot build; '
                             'choose a profile that edits code')
    actors = (tuple(observe_actor('builder', b) for b in builders)
              + tuple(observe_actor('reviewer', r) for r in reviewers))
    if len({a.profile for a in actors}) != len(actors) or not checks:
        raise ValueError('distinct builder/reviewer profiles and checks are required')
    from .contracts import identity, relative_path, string_tuple
    for name in ('repo', 'base', 'goal'):
        identity(spec[name], name)
    for name in ('acceptance', 'non_goals', 'write_paths', 'forbidden_paths'):
        string_tuple(tuple(spec[name]), name)
    if not spec['acceptance'] or not spec['write_paths'] or not spec['forbidden_paths']:
        raise ValueError('acceptance and scope paths are required')
    for key in ('write_paths', 'forbidden_paths'):
        for path in spec[key]:
            try:
                relative_path(path)
            except ValueError:
                # The coordinator's own value, echoed back so it can fix it without reading cpipe's source.
                raise ValueError(f'{key} entry {str(path)[:120]!r} must be a path relative to the repository root '
                                 'with no leading "/", "./" or "..", e.g. "src/app" or "src/app/main.py"') from None
    for path in spec['write_paths']:
        for denied in spec['forbidden_paths'] + ['.git']:
            if path == denied or path.startswith(denied + '/') or denied.startswith(path + '/'):
                raise ValueError('write allowance overlaps forbidden path')
    if len({c.obligation for c in checks}) != len(checks) or any(c.obligation == 'review' for c in checks):
        raise ValueError('check obligations must be unique and distinct from review')
    return artifact, checks, actors


def _git(root, *args, env=None, timeout=300):
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=root, env=env, check=True,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout).stdout.strip()


STARTS = ('cpipe: start from the uncommitted changes in ', 'cpipe: continue the work in ')


def starting_point(source, base, carry=True):
    """(commit, carried, base_commit) a delivery starts from: base, plus the checkout's uncommitted work when base
    is what the checkout has checked out (never for a draft PR). A checkout that is an earlier delivery's
    worktree (only cpipe's own start commits on top of base) is continued: all its work comes along as
    uncommitted edits on base, so the checks and the review cover it too. Nothing in the checkout changes."""
    try:
        commit = _git(source, 'rev-parse', '--verify', '-q', base + '^{commit}')
    except subprocess.CalledProcessError:
        raise ValueError('base must be a branch, tag or commit in the repository') from None
    head = _git(source, 'rev-parse', 'HEAD')
    continued = commit != head and _continues(source, commit, head)
    if not continued and (not carry or commit != head):
        return commit, False, commit
    # Other repositories inside the checkout (worktrees, clones) are not part of this one.
    nested = [n[:-1] for n in _git(source, 'ls-files', '-z', '--others', '--exclude-standard').split('\0')
              if n.endswith('/')]
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, 'GIT_INDEX_FILE': str(Path(tmp) / 'index')}
        _git(source, 'read-tree', 'HEAD', env=env)
        _git(source, 'add', '-A', '--', '.', *(':(exclude,literal)' + n for n in nested), env=env)
        tree = _git(source, 'write-tree', env=env)
    if tree == _git(source, 'rev-parse', commit + '^{tree}'):
        return commit, False, commit
    start = _git(source, '-c', 'user.name=cpipe', '-c', 'user.email=cpipe@localhost', 'commit-tree', '--no-gpg-sign',
                 tree, '-p', commit, '-m', STARTS[continued] + str(source))
    return start, True, commit if continued else start


def _continues(source, base, head):
    """Whether head is base plus only cpipe's own start commits: the checkout is an earlier delivery's worktree."""
    try:
        _git(source, 'merge-base', '--is-ancestor', base, head)
    except subprocess.CalledProcessError:
        return False
    return all(line.startswith(STARTS) for line in _git(source, 'log', '--format=%s', base + '..' + head).splitlines())


def isolate(source, start, delivery_id, branch=''):
    """A worktree of its own for each delivery, next to the repository in <repo>-worktrees: the user's checkout is
    never touched, and deliveries run side by side."""
    common = Path(_git(source, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    repo = common.parent if common.name == '.git' else Path(_git(source, 'rev-parse', '--show-toplevel'))
    parent = repo.parent / (repo.name + '-worktrees')
    parent.mkdir(parents=True, exist_ok=True)
    path = parent.resolve() / delivery_id
    _git(source, 'worktree', 'add', '-q', '-b', branch or 'cpipe/' + delivery_id, str(path), start)
    provision(source, path)
    return path


# Dependency lockfiles: when one differs, the checkout's installed dependencies are the wrong versions.
LOCKFILES = frozenset({'package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lockb',
                       'bun.lock', 'uv.lock', 'poetry.lock', 'Pipfile.lock', 'requirements.txt', 'Gemfile.lock',
                       'composer.lock', 'Cargo.lock', 'go.sum', 'mix.lock', 'pubspec.lock', 'Podfile.lock'})


def provision(source, path):
    """Start the delivery worktree with the checkout's installed dependencies (node_modules, tool caches and
    other ignored folders), so builders and checks never spend time and tokens reinstalling them. Copy-on-write
    clones only: a folder is skipped, never linked, so nothing can write back into the checkout. Skipped
    entirely when a lockfile differs, and for virtualenvs, whose scripts and editable installs point back at the
    checkout's own code. Best effort: a skipped folder is installed by the check command as before."""
    source, clone = Path(source), (['cp', '-c', '-R'] if sys.platform == 'darwin' else ['cp', '-R', '--reflink=always'])
    try:
        for name in _git(path, 'ls-files', '-z').split('\0'):
            if Path(name).name in LOCKFILES and (path / name).read_bytes() != (
                    (source / name).read_bytes() if (source / name).is_file() else b''):
                return
        ignored = _git(source, 'ls-files', '-z', '--others', '--ignored', '--exclude-standard', '--directory')
    except (subprocess.SubprocessError, OSError):
        return
    for name in ignored.split('\0'):
        src, dst = source / name, path / name
        if (not name.endswith('/') or dst.exists() or src.is_symlink() or (src / 'pyvenv.cfg').exists()
                or (src / '.git').exists() or next(src.glob('*/.git'), None)):
            continue  # not a folder, already there, a virtualenv, or another repository or worktree
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            done = subprocess.run([*clone, str(src).rstrip('/'), str(dst).rstrip('/')], capture_output=True,
                                  stdin=subprocess.DEVNULL, timeout=300).returncode == 0
        except subprocess.TimeoutExpired:
            done = False
        if not done:
            shutil.rmtree(dst, ignore_errors=True)


def discard(source, path, branch=''):
    """Remove a worktree cpipe made for a delivery that was never created."""
    for args in (('worktree', 'remove', '--force', str(path)), ('branch', '-D', branch or 'cpipe/' + Path(path).name)):
        try:
            _git(source, *args)
        except (subprocess.SubprocessError, OSError):
            pass


def from_spec(service, spec, *, session_id, profile_id, approach='', start=None, base_commit=None):
    if not session_id or not profile_id:
        raise ValueError('missing initiating session/profile')
    _, checks, actors = validate_spec(spec)
    if not start:
        start, _, base_commit = starting_point(spec['worktree'], spec['base'], carry=not spec.get('draft_pr'))
    base_commit = base_commit or start
    if spec.get('draft_pr'):
        from .publish import preflight
        preflight(spec['worktree'], spec['base'], base_commit, spec['branch'])
    delivery_id = 'cp-' + uuid.uuid4().hex
    root = isolate(spec['worktree'], start, delivery_id, spec.get('branch', ''))
    try:
        if base_commit != start:  # continued work: uncommitted edits on base, checked and reviewed with the rest
            _git(root, 'reset', '-q', '--soft', base_commit)
        return propose(service, _plan(spec, checks, actors, root, base_commit, delivery_id, session_id, profile_id,
                                      approach), checks)
    except BaseException:
        discard(spec['worktree'], root, spec.get('branch', ''))
        raise


def _plan(spec, checks, actors, root, start, delivery_id, session_id, profile_id, approach):
    from .artifact import fingerprint
    artifact = fingerprint(root)
    now = int(time.time())
    e = Envelope(delivery_id, session_id, profile_id, spec['repo'], artifact.branch,
        spec['base'], 'scope-' + uuid.uuid4().hex, tuple(c.obligation for c in checks) + ('review',),
        DRAFT if spec.get('draft_pr') else frozenset({'local'}))
    p = ExecutionPlan(envelope=e, worktree=str(root), common_git_dir=artifact.common_git_dir,
        base_commit=start, goal=spec['goal'], acceptance=tuple(spec['acceptance']),
        non_goals=tuple(spec['non_goals']), write_paths=tuple(spec['write_paths']),
        forbidden_paths=tuple(spec['forbidden_paths']), actors=actors, budget=Budget(spec['seconds'], CALLS),
        policy_id='draft-pr' if spec.get('draft_pr') else 'local-only', check_definition='frozen-command',
        checks=tuple(c.identity() for c in checks) + (review_identity(),), interactive=False,
        repair_allowed=False, transient_retries=0, completion_target='draft' if spec.get('draft_pr') else 'local_only',
        expires_at=now + APPROVAL_WINDOW + spec['seconds'],
        approach=approach, title=spec.get('title') or '')
    return p


def owned(service, delivery_id, session_id, profile_id):
    state = service.read(delivery_id)
    if state is None or (state.plan.envelope.session_id, state.plan.envelope.profile_id) != (session_id, profile_id):
        raise ValueError('missing or wrong-session/profile delivery')
    return state


def cancel(service, delivery_id, *, session_id, profile_id):
    state = owned(service, delivery_id, session_id, profile_id)
    if state.phase in {'cancelled', 'stopped', 'completed'}:
        return summary(state)
    stop_orphan(state.plan.worktree)
    e = state.plan.envelope
    state, _ = service.accept(Event('cancel:' + state.active_intent, 'cancelled', delivery_id,
        state.plan.digest(), state.artifact, state.version, max(state.last_at, int(time.time())),
        session_id=e.session_id, profile_id=e.profile_id))
    return summary(state)


def resume(service, delivery_id, *, session_id, profile_id):
    """Continue a delivery that stopped or waits, from the code as it is now. Out of time or
    calls, it goes back to approval for a fresh budget instead."""
    state = owned(service, delivery_id, session_id, profile_id)
    release(state.plan)  # files a crashed baseline moved aside
    e = state.plan.envelope
    state, _ = service.accept(Event('resume:' + uuid.uuid4().hex, 'resumed', delivery_id,
        state.plan.digest(), state.artifact, state.version, max(state.last_at, int(time.time())),
        session_id=e.session_id, profile_id=e.profile_id, result_artifact=validate_worktree(state.plan).digest))
    return state


def ready(service, delivery_id, session_id, profile_id):
    """Refuse execution unless the delivery is approved, unchanged and runnable; return its frozen checks."""
    state = owned(service, delivery_id, session_id, profile_id)
    if not state.approved:
        raise ValueError('execution requires an approved frozen envelope')
    row = service.journal.db.execute('SELECT payload FROM intent WHERE id=?',
                                    ('check-spec:' + state.plan.digest(),)).fetchone()
    if not row:
        raise ValueError('missing frozen check specifications')
    specs = json.loads(row[0])
    checks = tuple(CommandCheck(c['obligation'], tuple(c['argv']), tuple(c['criteria']), c['timeout_seconds'],
                                tuple(tuple(p) for p in c.get('env', ()))) for c in specs)
    frozen = state.plan.checks[:-1]
    if state.plan.checks[-1:] != (review_identity(),) or [(c.obligation, c.identity().inputs, c.identity().command)
            for c in checks] != [(a.obligation, a.inputs, a.command) for a in frozen]:
        raise ValueError('frozen check definitions changed')
    # Plans frozen before environments were stored ran in their host's settings: keep the approved identity
    # instead of stranding the delivery whenever another process (or a restarted app) resumes it.
    checks = tuple(c if 'env' in spec else replace(c, pinned=a.environment)
                   for c, spec, a in zip(checks, specs, frozen))
    if tuple(c.identity() for c in checks) != frozen:
        raise ValueError('frozen check environment changed')
    return state, checks


# Rounds of writing and verifying a pull request before the run waits for the user.
PR_ROUNDS = 2
CONVENTIONS = ("the repository's and your own instructions and skills for branch names, commit messages, pull "
               "request titles and descriptions (PR templates, contributing guides, linters)")


def pull_request(plan, payload, changed):
    """The writer's commits, title and body, or why they cannot be published."""
    from .review import redacted
    if type(payload) is not dict or set(payload) != {'commits', 'title', 'body'}:
        raise ValueError('return exactly commits, title and body')
    title, body, commits = payload['title'], payload['body'], payload['commits']
    if type(title) is not str or not 0 < len(title) <= 256 or '\n' in title.strip():
        raise ValueError('title must be one line of at most 256 characters')
    if type(body) is not str or not body.strip() or len(body) > 60000:
        raise ValueError('body must be non-empty and under 60000 characters')
    if (type(commits) is not list or not 0 < len(commits) <= 10
            or any(type(c) is not dict or set(c) != {'message', 'paths'} or type(c['message']) is not str
                   or not c['message'].strip() or len(c['message']) > 8000 or type(c['paths']) is not list
                   or not c['paths'] or any(type(n) is not str for n in c['paths']) for c in commits)):
        raise ValueError('commits must be 1 to 10 of {message, paths}')
    paths = [n for c in commits for n in c['paths']]
    if len(paths) != len(set(paths)) or set(paths) != set(changed):
        raise ValueError('the commits must cover every changed path exactly once: ' + ', '.join(sorted(changed)))
    for text in (title.strip(), body, *(c['message'] for c in commits)):
        if any(marker in text for marker in (plan.envelope.delivery_id, plan.worktree, str(Path.home()))):
            raise ValueError('internal workflow data (a delivery id or local path) is not for reviewers')
        if redacted(text, len(text)) != text:
            raise ValueError('a credential or secret is not for reviewers')
    return [(c['paths'], c['message'].strip() + '\n') for c in commits], title.strip(), body.strip() + '\n'


def publish(service, bridge, delivery_id, tid, claimer, ask=None):
    """The builder writes the commits, title and description the user's conventions ask for; each commit is
    pushed on its own and the draft PR opened; the reviewer then verifies the published PR against the
    instructions. Recorded once GitHub shows it and the reviewer approves it."""
    from .hermes_worker import ask as worker_ask
    from .publish import open_draft, readback, trees
    from .service import actor
    ask = ask or worker_ask
    state = owned(service, delivery_id, bridge.session_id, bridge.profile_id)
    plan = state.plan
    if validate_worktree(plan).digest != state.artifact:
        raise ValueError('the worktree changed after it was verified')
    changed = changed_since_base(plan)
    if any(inside(n, plan.forbidden_paths) for n in changed):
        raise ValueError('the worktree has changes in a forbidden path')

    def heartbeat():
        if not h.heartbeat_claim(bridge.conn, tid, claimer=claimer, ttl_seconds=900):
            raise ValueError('native ownership could not be renewed')
    latest = {r.obligation: r.outcome.value for r in state.receipts}
    brief = encoded(dict(goal=plan.goal, acceptance=list(plan.acceptance), non_goals=list(plan.non_goals),
                         base=plan.envelope.base, branch=plan.envelope.branch, changed=changed,
                         checks={c.obligation: latest.get(c.obligation, 'not run') for c in plan.checks},
                         review_rounds=state.review_cycles))
    problems, url = [], ''
    for _ in range(PR_ROUNDS):
        prompt = ('The change in this worktree is finished: its checks and an independent code review passed. '
                  'Write its commits, pull request title and description for the people who review it, following '
                  + CONVENTIONS + '. If a linter for them is configured, run it. Describe the problem, the change '
                  'and how it was verified; never include internal workflow data (delivery or plan IDs, local '
                  'paths, agent or tool names, budgets). Read the diff with git diff ' + plan.base_commit +
                  '. Do not edit files, commit, push or open anything. Return only {"commits":[{"message":"…",'
                  '"paths":["…"]}],"title":"…","body":"…"}: commits in order, together covering every changed path '
                  'exactly once; one commit unless the instructions or conventions ask for more.\nDelivery: ' + brief
                  + ('\nThe last version did not pass verification; fix these problems:\n- ' + '\n- '.join(problems)
                     if problems else ''))
        for attempt in range(2):
            payload = ask(actor(state, 'builder'), plan.worktree, prompt, heartbeat=heartbeat, timeout_seconds=600)
            try:
                commits, title, body = pull_request(plan, payload, changed)
                break
            except ValueError as exc:
                if attempt:
                    raise ValueError('the pull request content could not be used: ' + str(exc)) from None
                prompt += '\nYour last answer could not be used: ' + str(exc)
        if validate_worktree(plan).digest != state.artifact:
            raise ValueError('the worktree changed while the pull request was written')
        url, commit = open_draft(plan, list(zip(trees(plan.worktree, plan.base_commit, [p for p, _ in commits]),
                                                (m for _, m in commits))), title, body)
        service.journal.db.execute('INSERT OR REPLACE INTO intent(id,payload,acknowledged) VALUES (?,?,1)',
                                   ('pr:' + delivery_id, encoded(dict(url=url, commit=commit))))
        verdict = ask(actor(state, 'reviewer'), plan.worktree, (
            'Verify a published draft pull request before the user is told it is done. Check it against the '
            "user's instructions below (goal, acceptance, non-goals) and against " + CONVENTIONS + ': the branch '
            'name, every commit message and the files in each commit, the title and the description. It must '
            'carry no internal workflow data (delivery or plan IDs, local paths, agent or tool names). A problem '
            'breaks an instruction or a convention; style preferences are not problems. Read-only: change '
            'nothing. Return only {"status":"approved"} or {"status":"changes_requested","problems":["<one line '
            'each>"]}.\nInstructions: ' + brief + '\nPublished: ' + encoded(readback(plan))),
            heartbeat=heartbeat, timeout_seconds=600)
        if verdict == {'status': 'approved'}:
            break
        found = verdict.get('problems') if type(verdict) is dict and verdict.get('status') == 'changes_requested' else None
        if type(found) is not list or not found or any(type(x) is not str for x in found):
            raise ValueError('the pull request verification gave no usable verdict')
        from .review import redacted
        problems = [redacted(x, 300) for x in found[:10]]
    else:
        raise ValueError(f'the draft pull request {url} does not meet the instructions yet: ' + '; '.join(problems))
    e = plan.envelope
    event = bridge.result_event(delivery_id, tid, claimer=claimer, kind='publication_observed',
                                target=(e.repo, e.branch, e.base, state.artifact, 'draft'))
    state = bridge.finish(event, tid, claimer=claimer, summary='Opened and verified draft pull request ' + url)
    try:  # the local branch matches what was pushed; the files are untouched
        _git(plan.worktree, 'reset', '-q', commit)
    except (subprocess.SubprocessError, OSError):
        pass
    return state


def run(service, delivery_id, *, session_id, profile_id, conn=None, execute=None, evidence_root):
    state, checks = ready(service, delivery_id, session_id, profile_id)
    if conn is None:
        h.kb.init_db()
        with closing(h.connect(None)) as conn:
            return run(service, delivery_id, session_id=session_id, profile_id=profile_id,
                       conn=conn, execute=execute, evidence_root=evidence_root)
    bridge = NativeBridge(service, conn, session_id=session_id, profile_id=profile_id)
    worker, steps, recoveries, strikes, baselined, shown = execute or HermesWorker(), [], [], {}, False, ''
    worker.checks = checks  # the test writer runs the exact behavior check before returning
    tid = None

    def note(text):
        recoveries.append(text)
        tell(session_id, delivery_id, text)
        if tid:
            bridge.comment(tid, text)  # handoffs and retries show as card activity on the board
    store = Path(evidence_root) / 'good' / delivery_id
    claimer = h.new_claimer()
    bridge.abandon(delivery_id)  # recover a run left bound by a crashed build,
    stop_orphan(state.plan.worktree)  # the worker it left running
    release(state.plan)  # and files its baseline moved aside
    for _ in range(max(4, state.plan.budget.calls * 6)):
        state = owned(service, delivery_id, session_id, profile_id)
        if state.activity not in ACTIVE_STEPS:
            break
        if time.time() >= deadline(state):
            # Record the stop instead of failing every step on the expired budget.
            e = state.plan.envelope
            state, _ = service.accept(Event('deadline:' + state.active_intent, 'deadline_reached', delivery_id,
                state.plan.digest(), state.artifact, state.version, max(state.last_at, int(time.time())),
                session_id=e.session_id, profile_id=e.profile_id))
            if state.renewals >= AUTO_RENEWALS or state.calls_used >= state.plan.budget.calls:
                break
            # Time is a stall guard, calls are the spend guard: with calls left, keep going.
            resume(service, delivery_id, session_id=session_id, profile_id=profile_id)
            note('the time budget ran out with calls left; renewed it and continuing')
            continue
        action, tid = state.activity, None
        try:
            state = settle(state, store, service)
            action = state.activity
            name, place = bridge.progress(state)
            line = (f'[{place}] ' if place else '') + f'{name} · ' + STEP_LINES[action].format(
                who=bridge._actor(state).profile if action in WORKER_STEPS else '')
            if line != shown:
                tell(session_id, delivery_id, shown := line)
            tid = bridge.schedule(delivery_id, claimer)
            if bridge.replaced:
                note(f'{action}: replaced {len(bridge.replaced)} card(s) that something outside '
                                  'cpipe had taken, changed or left behind')
                bridge.replaced.clear()
            if tid is None:
                raise ValueError('missing runnable native task')
            if action in {'tests', 'checks'}:
                if h.take(conn, tid, claimer=claimer, ttl_seconds=state.plan.budget.seconds) is None:
                    raise ValueError('task owned by another launcher; no worker started')
                bridge.bind_claim(delivery_id, tid, claimer=claimer, worker_session='check-' + uuid.uuid4().hex)
                if action in {'tests', 'checks'} and not baselined and not baseline_path(evidence_root, state).exists():
                    baselined = True  # once per run: a failing baseline must not double every check
                    try:  # what already failed before this change is not its doing; runs on the base code
                        with at_base(state.plan, changed_since_base(state.plan)):
                            take_baseline(state, checks, evidence_root=Path(evidence_root))
                    except Exception as exc:
                        note(f'could not record which checks already failed ({type(exc).__name__}); '
                                          'every failure counts')
                # tests proves the behavior check before implementing; checks runs everything.
                due = checks if action == 'checks' else tuple(c for c in checks if c.obligation == 'behavior')
                state = bridge.observe_checks(delivery_id, tid, claimer=claimer,
                                              checks=due, evidence_root=Path(evidence_root))
            elif action in {'write_tests', 'implement'}:
                state = observe_implementation(bridge, delivery_id, tid, claimer=claimer, execute=worker)
            elif action == 'publish':
                if h.take(conn, tid, claimer=claimer, ttl_seconds=900) is None:
                    raise ValueError('task owned by another launcher; no worker started')
                bridge.bind_claim(delivery_id, tid, claimer=claimer, worker_session='publish-' + uuid.uuid4().hex)
                try:
                    state = publish(service, bridge, delivery_id, tid, claimer)
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    if owned(service, delivery_id, session_id, profile_id).activity != 'publish':
                        raise
                    from .review import redacted
                    detail = getattr(exc, 'stderr', '') or str(exc)
                    note('the draft pull request is not done: ' + redacted(str(detail).strip(), 600) +
                         '. The verified work stays in the worktree; fix the cause and say continue to retry.')
                    bridge.abandon(delivery_id, claimer, kind='activity_refused')
                    continue
            else:
                state = observe_review(bridge, delivery_id, tid, claimer=claimer, execute=worker,
                                       evidence_root=Path(evidence_root))
        except WorkerRefused as exc:
            # Not a crash: the next approved actor takes over, or the run waits for the user. A missing
            # tool or dependency stops every actor alike, so it waits for the user straight away.
            note(f'{bridge._actor(state).profile} declined {action}: {exc}')
            bridge.abandon(delivery_id, claimer, kind='activity_refused',
                           action='environment' if str(exc).lower().startswith('environment') else '')
            continue
        except Exception as exc:
            current = owned(service, delivery_id, session_id, profile_id)
            if current.activity != action or not current.approved:
                continue  # cancelled, out of budget or moved on meanwhile; the loop decides
            # cpipe's own ValueErrors are fixed labels; anything else stays a type name.
            reason = str(exc)[:200] if type(exc) is ValueError else type(exc).__name__
            if action not in WORKER_STEPS or not current.run_id:
                # Not the worker's doing (checks, or preparing the step): retry a few times, then report.
                strikes[action] = n = strikes.get(action, 0) + 1
                note(f'{action} failed ({reason}); ' + ('retrying' if n < FAILURES else 'giving up'))
                if current.run_id:
                    bridge.abandon(delivery_id, claimer)
                if n >= FAILURES:
                    raise
            else:
                who = bridge._actor(state).profile
                strikes[who] = n = strikes.get(who, 0) + 1
                note(f'{who} failed {action} ({reason}); '
                                  + ('retrying' if n < FAILURES else 'handing over'))
                worker.note = (f'Your previous attempt at this step failed: {reason}. Its changes were undone. '
                               'Do the step again and finish with exactly the requested JSON.')
                if n < FAILURES:
                    # A session that failed once carries the failure's context into every resume.
                    bridge.abandon(delivery_id, claimer, action='fresh')
                else:
                    worker.note = ''
                    bridge.abandon(delivery_id, claimer, kind='activity_refused')
            time.sleep(min(BACKOFF * n, max(0, deadline(state) - time.time()) / 4))
            continue
        strikes.pop(bridge._actor(state).profile if action in WORKER_STEPS else action, None)  # the ladder restarts
        task = h.get_task(conn, tid)
        steps.append(dict(activity=action, task_id=tid, native_status=task.status,
                          phase=state.phase, outcomes=[r.outcome.value for r in state.receipts]))
    result = summary(owned(service, delivery_id, session_id, profile_id), service)
    result['steps'] = steps
    if recoveries:
        result['recoveries'] = recoveries
    return result
