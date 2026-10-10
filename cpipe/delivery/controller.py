"""v2-only production local controller, sharing the receipt-aware native adapters.

Trusted local workers are not sandboxed. Publication is deliberately unavailable
rather than falling back to the removed legacy delivery pipeline.
"""
from contextlib import closing
import json
from pathlib import Path
import time
import uuid

from .artifact import validate_worktree
from .check_runner import PytestCheck
from .contracts import Envelope, Budget
from .schema import ExecutionPlan
from .service import Service, Event
from .native_bridge import NativeBridge
from .hermes_worker import HermesWorker
from .workers import observe_implementation
from .review import observe_review, review_identity
from .approval import arguments
from .journal import encoded
from ..runtime import hermes_api as h

# Time a frozen plan may wait for consent; the execution budget starts at approval.
APPROVAL_WINDOW = 3600


def summary(state):
    result = dict(engine_version=2, delivery_id=state.plan.envelope.delivery_id,
        phase=state.phase, activity=state.activity, artifact=state.artifact,
        approved=state.approved, review_cycles=state.review_cycles,
        outcomes=[r.outcome.value for r in state.receipts],
        completion_target=state.plan.completion_target)
    if state.phase == 'proposal' and not state.approved:
        result['approval_arguments'] = arguments(state)
        result['plan'] = state.plan.to_dict()
    return result


def propose(service, plan, checks):
    if (plan.completion_target != 'local_only' or plan.envelope.capabilities != frozenset({'local'})
            or plan.interactive or plan.repair_allowed or plan.transient_retries
            or any(a.engine != 'hermes' for a in plan.actors)
            or len({a.profile for a in plan.actors}) != 2
            or len(checks) == 0 or any(type(c) is not PytestCheck for c in checks)
            or tuple(c.identity() for c in checks) + (review_identity(),) != plan.checks):
        raise ValueError('cpipe supports explicit local-only Hermes execution with pytest and independent review')
    artifact = validate_worktree(plan)
    now = int(time.time())
    e = plan.envelope
    state, _ = service.accept(Event('proposal:' + plan.digest(), 'proposal_created', e.delivery_id,
        plan.digest(), artifact.digest, 0, now, session_id=e.session_id, profile_id=e.profile_id, plan=plan))
    # Specs are frozen alongside the projection; replay may never change them.
    key = 'check-spec:' + plan.digest()
    payload = encoded([dict(obligation=c.obligation, targets=list(c.targets), criteria=list(c.criteria),
                          timeout_seconds=c.timeout_seconds, **({'python': c.python} if c.python else {}))
                       for c in checks])
    row = service.journal.db.execute('SELECT payload FROM intent WHERE id=?', (key,)).fetchone()
    if row and row[0] != payload:
        raise ValueError('check definition changed')
    service.journal.db.execute('INSERT OR IGNORE INTO intent(id,payload,acknowledged) VALUES (?,?,1)', (key, payload))
    return state


def validate_spec(spec):
    """Preflight a draft without saving a proposal or starting its budget."""
    from .live_probe import observe_actor
    from .artifact import fingerprint
    expected = {'worktree', 'repo', 'base', 'goal', 'acceptance', 'non_goals', 'write_paths',
                'forbidden_paths', 'builder', 'reviewer', 'seconds', 'checks'}
    if type(spec) is not dict or set(spec) != expected:
        raise ValueError('provide the exact specification fields; unsupported arguments are rejected')
    if type(spec['seconds']) is not int or not 30 <= spec['seconds'] <= 1800:
        raise ValueError('cpipe requires a bounded 30..1800 second budget')
    for key in ('acceptance', 'non_goals', 'write_paths', 'forbidden_paths', 'checks'):
        if type(spec[key]) is not list:
            raise ValueError('cpipe requires explicit list fields')

    root = Path(spec['worktree'])
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError('worktree must be an absolute non-symlink directory')
    artifact = fingerprint(root)
    checks = []
    venv = root / '.venv' / 'bin' / 'python'
    for item in spec['checks']:
        fields = {'obligation', 'targets', 'criteria', 'timeout_seconds'}
        if type(item) is not dict or not fields <= set(item) <= fields | {'python'}:
            raise ValueError('invalid frozen pytest specification')
        if type(item['targets']) is not list or type(item['criteria']) is not list:
            raise ValueError('invalid frozen pytest test lists')
        python = item.get('python', str(venv) if venv.exists() else '')
        checks.append(PytestCheck(item['obligation'], tuple(item['targets']), tuple(item['criteria']),
                                  item['timeout_seconds'], python))
    checks = tuple(checks)
    if not any(c.obligation == 'behavior' for c in checks):
        raise ValueError('one check must have obligation "behavior": the test proving this change, run before implementing')
    import importlib.util
    if any(not c.python for c in checks) and importlib.util.find_spec('pytest') is None:
        raise ValueError('no project interpreter with pytest; set checks[].python (e.g. <worktree>/.venv/bin/python)')
    for c in checks:
        c.identity()  # probes the interpreter now, not after approval
    actors = (observe_actor('builder', spec['builder']), observe_actor('reviewer', spec['reviewer']))
    if len({a.profile for a in actors}) != 2 or not checks:
        raise ValueError('distinct builder/reviewer profiles and checks are required')
    from .contracts import identity, relative_path, string_tuple
    for name in ('repo', 'base', 'goal'):
        identity(spec[name], name)
    for name in ('acceptance', 'non_goals', 'write_paths', 'forbidden_paths'):
        string_tuple(tuple(spec[name]), name)
    if not spec['acceptance'] or not spec['write_paths'] or not spec['forbidden_paths']:
        raise ValueError('acceptance and scope paths are required')
    for path in spec['write_paths'] + spec['forbidden_paths']:
        relative_path(path)
    for path in spec['write_paths']:
        for denied in spec['forbidden_paths'] + ['.git']:
            if path == denied or path.startswith(denied + '/') or denied.startswith(path + '/'):
                raise ValueError('write allowance overlaps forbidden path')
    if len({c.obligation for c in checks}) != len(checks) or any(c.obligation == 'review' for c in checks):
        raise ValueError('check obligations must be unique and distinct from review')
    return artifact, checks, actors


def from_spec(service, spec, *, session_id, profile_id, approach=''):
    if not session_id or not profile_id:
        raise ValueError('missing initiating session/profile')
    artifact, checks, actors = validate_spec(spec)
    root = Path(spec['worktree'])
    now = int(time.time())
    e = Envelope('v2-' + uuid.uuid4().hex, session_id, profile_id, spec['repo'], artifact.branch,
        spec['base'], 'scope-' + uuid.uuid4().hex, tuple(c.obligation for c in checks) + ('review',), frozenset({'local'}))
    p = ExecutionPlan(envelope=e, worktree=str(root), common_git_dir=artifact.common_git_dir,
        base_commit=artifact.head, goal=spec['goal'], acceptance=tuple(spec['acceptance']),
        non_goals=tuple(spec['non_goals']), write_paths=tuple(spec['write_paths']),
        forbidden_paths=tuple(spec['forbidden_paths']), actors=actors, budget=Budget(spec['seconds'], 8),
        policy_id='v2-local-only', check_definition='v2-frozen-pytest',
        checks=tuple(c.identity() for c in checks) + (review_identity(),), interactive=False,
        repair_allowed=False, transient_retries=0, completion_target='local_only', expires_at=now + APPROVAL_WINDOW + spec['seconds'],
        approach=approach)
    return propose(service, p, checks)


def owned(service, delivery_id, session_id, profile_id):
    state = service.read(delivery_id)
    if state is None or (state.plan.envelope.session_id, state.plan.envelope.profile_id) != (session_id, profile_id):
        raise ValueError('missing or wrong-session/profile v2 delivery')
    return state


def cancel(service, delivery_id, *, session_id, profile_id):
    state = owned(service, delivery_id, session_id, profile_id)
    if state.phase in {'cancelled', 'stopped', 'completed'}:
        return summary(state)
    e = state.plan.envelope
    state, _ = service.accept(Event('cancel:' + state.active_intent, 'cancelled', delivery_id,
        state.plan.digest(), state.artifact, state.version, max(state.last_at, int(time.time())),
        session_id=e.session_id, profile_id=e.profile_id))
    return summary(state)


def run(service, delivery_id, *, session_id, profile_id, conn=None, execute=None, evidence_root):
    state = owned(service, delivery_id, session_id, profile_id)
    if not state.approved:
        raise ValueError('v2 execution requires an approved frozen envelope')
    from .approach import validate_document
    validate_document(service, state.plan)
    if state.plan.completion_target != 'local_only':
        raise ValueError('v2 publication adapter is not installed')
    row = service.journal.db.execute('SELECT payload FROM intent WHERE id=?',
                                    ('check-spec:' + state.plan.digest(),)).fetchone()
    if not row:
        raise ValueError('missing frozen check specifications')
    checks = tuple(PytestCheck(c['obligation'], tuple(c['targets']), tuple(c['criteria']), c['timeout_seconds'],
                               c.get('python', '')) for c in json.loads(row[0]))
    if tuple(c.identity() for c in checks) + (review_identity(),) != state.plan.checks:
        raise ValueError('frozen check environment changed')
    if conn is None:
        h.kb.init_db()
        with closing(h.connect(None)) as conn:
            return run(service, delivery_id, session_id=session_id, profile_id=profile_id,
                       conn=conn, execute=execute, evidence_root=evidence_root)
    bridge = NativeBridge(service, conn, session_id=session_id, profile_id=profile_id)
    worker, steps = execute or HermesWorker(), []
    claimer = h.new_claimer()
    for _ in range(max(4, state.plan.budget.calls * 4)):
        state = owned(service, delivery_id, session_id, profile_id)
        if not state.activity or state.activity not in {'tests', 'checks', 'implement', 'review'}:
            break
        action = state.activity
        tid = bridge.schedule(delivery_id)
        if tid is None:
            raise ValueError('missing runnable v2 native task')
        if action in {'tests', 'checks'}:
            if h.claim_task(conn, tid, claimer=claimer, ttl_seconds=state.plan.budget.seconds) is None:
                raise ValueError('v2 task owned by another launcher; no worker started')
            bridge.bind_claim(delivery_id, tid, claimer=claimer, worker_session='check-' + uuid.uuid4().hex)
            # tests proves the behavior check before implementing; checks runs everything.
            due = checks if action == 'checks' else tuple(c for c in checks if c.obligation == 'behavior')
            state = bridge.observe_checks(delivery_id, tid, claimer=claimer,
                                          checks=due, evidence_root=Path(evidence_root))
        elif action == 'implement':
            state = observe_implementation(bridge, delivery_id, tid, claimer=claimer, execute=worker)
        else:
            state = observe_review(bridge, delivery_id, tid, claimer=claimer, execute=worker,
                                   evidence_root=Path(evidence_root))
        task = h.get_task(conn, tid)
        steps.append(dict(activity=action, task_id=tid, native_status=task.status,
                          phase=state.phase, outcomes=[r.outcome.value for r in state.receipts]))
    result = summary(owned(service, delivery_id, session_id, profile_id))
    result['steps'] = steps
    return result
