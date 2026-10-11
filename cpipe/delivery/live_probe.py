"""Explicit live-model native-adapter probe, not production/UI integration.

Creates a disposable repository and native database outside the live board. It
uses synthetic fixture-only approval, calls independently profiled Hermes
workers, and observes RED -> edit -> GREEN -> independent review. No publication,
ordinary project changes, credentials or personal roster configuration are saved.
The workers are trusted, local and unsandboxed, not confined to the fixture.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from .artifact import fingerprint
from .check_runner import CommandCheck, REPORT
from .contracts import Budget, Envelope
from .schema import Actor, ExecutionPlan
from .journal import Journal
from .service import Service, Event
from .native_bridge import NativeBridge
from .hermes_worker import HermesWorker
from .workers import observe_implementation
from .review import observe_review, review_identity
from ..runtime import hermes_api as h


def observe_actor(role, profile):
    """Freeze saved model/provider identifiers; never observe credential fields."""
    import yaml
    from ..routing import profile_home
    try:
        config = yaml.safe_load((profile_home(profile) / 'config.yaml').read_text())
        model = config['model']
        return Actor(role, 'hermes', profile, model['default'], model['provider'])
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        raise ValueError('probe requires an available profile with explicit model and provider') from None


def run(*, actors, scratch=None, execute=None, seconds=480, on_progress=None):
    if (type(actors) is not tuple or any(type(a) is not Actor for a in actors)
            or {a.role for a in actors} != {'builder', 'reviewer'}
            or len(actors) != 2 or actors[0].profile == actors[1].profile
            or any(a.engine != 'hermes' for a in actors)):
        raise ValueError('probe requires distinct frozen Hermes builder/reviewer actors')
    if type(seconds) is not int or not 30 <= seconds <= 1800:
        raise ValueError('probe requires a bounded 30..1800 second budget')
    if scratch is None:
        from hermes_constants import get_hermes_home
        scratch = get_hermes_home() / 'cache' / 'scratch'
    scratch = Path(scratch).absolute()
    if scratch.resolve() != scratch:
        raise ValueError('probe scratch must be non-symlink')
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = Path(tempfile.mkdtemp(prefix='cpipe-live-native-', dir=scratch))
    repo = root / 'repo'
    repo.mkdir()

    def git(*args):
        return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True,
                              text=True, timeout=20).stdout.strip()

    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (repo / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (repo / 'double.py').write_text('def double(n): return n\n')
    (repo / 'test_behavior.py').write_text(
        'from double import double\ndef test_double():\n'
        '    assert double(2) == 4\n    assert double(-3) == -6\n    assert double(0) == 0\n')
    git('add', '.')
    git('commit', '-m', 'test: establish failing fixture')
    git('switch', '-c', 'fixture')
    now = int(time.time())
    spec = CommandCheck('behavior', (sys.executable, '-m', 'pytest', 'test_behavior.py', '-q', '-p', 'no:cacheprovider',
                         '--junitxml=' + REPORT), ('test_behavior::test_double',))
    session = 'probe-' + uuid.uuid4().hex
    envelope = Envelope('live-probe', session, 'default', 'local/fixture', 'fixture', 'main',
                        'double-only', ('behavior', 'review'), frozenset({'local'}))
    p = ExecutionPlan(envelope=envelope, worktree=str(repo), common_git_dir=str(repo / '.git'),
        base_commit=git('rev-parse', 'HEAD'), goal='Fix double(n) to return n * 2.',
        acceptance=('double returns twice positive, negative, and zero inputs; frozen behavior check passes',),
        non_goals=('No dependencies, no test changes, no publication, no delegation',),
        write_paths=('double.py',), forbidden_paths=('.git', 'internal'), actors=actors,
        budget=Budget(seconds, 4), policy_id='fixture-only', check_definition='command-fixture',
        checks=(spec.identity(), review_identity()), interactive=False, repair_allowed=False,
        transient_retries=0, completion_target='local_only', expires_at=now + seconds)
    h.kb.init_db(root / 'native.db')
    conn = h.kbc.connect(root / 'native.db')
    steps = []
    result = dict(schema=1, root=str(root), approval='synthetic-harness-only',
                  production_wiring=False, phase='proposal', steps=steps)

    def checkpoint():
        (root / 'probe-result.json').write_text(json.dumps(result, indent=2))
        if on_progress:
            on_progress(result)

    try:
        with Journal(root / 'delivery.db') as journal:
            service = Service(journal)
            state, _ = service.accept(Event('proposal', 'proposal_created', 'live-probe', p.digest(),
                fingerprint(repo).digest, 0, now, session_id=session, profile_id='default', plan=p))
            state, _ = service.accept(Event('fixture-approval', 'envelope_approved', 'live-probe', p.digest(),
                state.artifact, state.version, now, session_id=session, profile_id='default',
                activity_id=state.active_intent))
            bridge = NativeBridge(service, conn, session_id=session, profile_id='default')
            worker = execute or HermesWorker()
            for _ in range(5):
                state = service.read('live-probe')
                if state is None:
                    raise ValueError('missing probe state')
                if not state.activity:
                    break
                action = state.activity
                tid = bridge.schedule('live-probe')
                if tid is None:
                    raise ValueError('missing native probe task')
                if action in {'tests', 'checks'}:
                    h.claim_task(conn, tid, claimer='probe-controller', ttl_seconds=seconds)
                    bridge.bind_claim('live-probe', tid, claimer='probe-controller', worker_session='probe-checks')
                    state = bridge.observe_checks('live-probe', tid, claimer='probe-controller',
                        checks=(spec,), evidence_root=root / 'evidence')
                elif action == 'implement':
                    state = observe_implementation(bridge, 'live-probe', tid, claimer='probe-controller', execute=worker)
                elif action == 'review':
                    state = observe_review(bridge, 'live-probe', tid, claimer='probe-controller',
                                           execute=worker, evidence_root=root / 'evidence')
                else:
                    raise ValueError('unsupported probe activity')
                result['phase'] = state.phase
                task = h.get_task(conn, tid)
                if task is None:
                    raise ValueError('native probe task disappeared')
                steps.append(dict(activity=action, task_id=tid,
                    native_status=task.status, phase=state.phase, artifact=state.artifact,
                    builder_session=state.builder_session, review_count=state.review_cycles,
                    outcomes=[r.outcome.value for r in state.receipts]))
                checkpoint()
            if state is None or state.phase != 'locally_ready':
                raise ValueError('live fixture did not reach local readiness')
            return result
    except Exception as exc:
        result.update(failed=True, error_type=type(exc).__name__, detail='[REDACTED]')
        checkpoint()
        raise ValueError('live adapter probe failed; inspect redacted native state at ' + str(root)) from None
    finally:
        conn.close()
