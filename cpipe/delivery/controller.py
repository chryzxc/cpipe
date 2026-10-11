"""Production local controller, sharing the receipt-aware native adapters.

Trusted local workers are not sandboxed. Publication is deliberately unavailable.
"""
from contextlib import closing
import json
from pathlib import Path
import subprocess
import time
import uuid

from .artifact import validate_worktree
from .check_runner import CommandCheck
from .contracts import Envelope, Budget, UNAVAILABLE_REASONS
from .schema import ExecutionPlan
from .service import Service, Event
from .native_bridge import NativeBridge
from .hermes_worker import changed_since_base, reattach, HermesWorker, keep, roll_back
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
ACTIVE_STEPS = frozenset({'tests', 'checks', 'write_tests', 'implement', 'review'})
BACKOFF = 15
WORKER_STEPS = frozenset({'write_tests', 'implement', 'review'})


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
    # Nothing verified to return to (or it is gone): continue from the worktree as it is, if it
    # stays inside the approved paths. The checks prove it again before any review.
    reattach(state.plan)
    plan = state.plan
    inside = lambda name, paths: any(name == p or name.startswith(p + '/') for p in paths)
    if any(not inside(n, plan.write_paths) or inside(n, plan.forbidden_paths) for n in changed_since_base(plan)):
        raise ValueError('the worktree was changed outside the approved paths and could not be restored')
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
    if state.phase == 'proposal' and not state.approved:
        result['approval_arguments'] = arguments(state)
        result['plan'] = state.plan.to_dict()
    if service is not None:
        result['tokens'] = usage(service, state.plan.envelope.delivery_id)
    return result


def propose(service, plan, checks):
    if (plan.completion_target != 'local_only' or plan.envelope.capabilities != frozenset({'local'})
            or plan.interactive or plan.repair_allowed or plan.transient_retries
            or any(a.engine != 'hermes' for a in plan.actors)
            or len({a.profile for a in plan.actors}) != len(plan.actors)
            or len(checks) == 0 or any(type(c) is not CommandCheck for c in checks)
            or tuple(c.identity() for c in checks) + (review_identity(),) != plan.checks):
        raise ValueError('cpipe supports explicit local-only Hermes execution with frozen checks and independent review')
    artifact = validate_worktree(plan)
    now = int(time.time())
    e = plan.envelope
    state, _ = service.accept(Event('proposal:' + plan.digest(), 'proposal_created', e.delivery_id,
        plan.digest(), artifact.digest, 0, now, session_id=e.session_id, profile_id=e.profile_id, plan=plan))
    # Specs are frozen alongside the projection; replay may never change them.
    key = 'check-spec:' + plan.digest()
    payload = encoded([dict(obligation=c.obligation, argv=list(c.argv), criteria=list(c.criteria),
                          timeout_seconds=c.timeout_seconds) for c in checks])
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
    if set(spec) != expected:
        # Field names are fixed schema labels, never user values.
        missing, extra = sorted(expected - set(spec)), sorted(set(spec) - expected)
        raise ValueError('spec fields: missing ' + (', '.join(missing) or 'none')
                         + '; unsupported ' + (', '.join(k for k in extra if k.isidentifier()) or 'none'))
    if type(spec['seconds']) is not int or not 30 <= spec['seconds'] <= 7200:
        raise ValueError('cpipe requires a bounded 30..7200 second budget')
    for key in ('acceptance', 'non_goals', 'write_paths', 'forbidden_paths', 'checks'):
        if type(spec[key]) is not list:
            raise ValueError('cpipe requires explicit list fields')

    root = Path(spec['worktree'])
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError('worktree must be an absolute non-symlink directory')
    artifact = fingerprint(root)
    checks = []
    for item in spec['checks']:
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
    e = Envelope('cp-' + uuid.uuid4().hex, session_id, profile_id, spec['repo'], artifact.branch,
        spec['base'], 'scope-' + uuid.uuid4().hex, tuple(c.obligation for c in checks) + ('review',), frozenset({'local'}))
    p = ExecutionPlan(envelope=e, worktree=str(root), common_git_dir=artifact.common_git_dir,
        base_commit=artifact.head, goal=spec['goal'], acceptance=tuple(spec['acceptance']),
        non_goals=tuple(spec['non_goals']), write_paths=tuple(spec['write_paths']),
        forbidden_paths=tuple(spec['forbidden_paths']), actors=actors, budget=Budget(spec['seconds'], CALLS),
        policy_id='local-only', check_definition='frozen-command',
        checks=tuple(c.identity() for c in checks) + (review_identity(),), interactive=False,
        repair_allowed=False, transient_retries=0, completion_target='local_only', expires_at=now + APPROVAL_WINDOW + spec['seconds'],
        approach=approach)
    return propose(service, p, checks)


def owned(service, delivery_id, session_id, profile_id):
    state = service.read(delivery_id)
    if state is None or (state.plan.envelope.session_id, state.plan.envelope.profile_id) != (session_id, profile_id):
        raise ValueError('missing or wrong-session/profile delivery')
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


def ready(service, delivery_id, session_id, profile_id):
    """Refuse execution unless the delivery is approved, unchanged and runnable; return its frozen checks."""
    state = owned(service, delivery_id, session_id, profile_id)
    if not state.approved:
        raise ValueError('execution requires an approved frozen envelope')
    from .approach import validate_document
    validate_document(service, state.plan)
    if state.plan.completion_target != 'local_only':
        raise ValueError('publication adapter is not installed')
    row = service.journal.db.execute('SELECT payload FROM intent WHERE id=?',
                                    ('check-spec:' + state.plan.digest(),)).fetchone()
    if not row:
        raise ValueError('missing frozen check specifications')
    checks = tuple(CommandCheck(c['obligation'], tuple(c['argv']), tuple(c['criteria']), c['timeout_seconds'])
                   for c in json.loads(row[0]))
    if tuple(c.identity() for c in checks) + (review_identity(),) != state.plan.checks:
        raise ValueError('frozen check environment changed')
    return state, checks


def run(service, delivery_id, *, session_id, profile_id, conn=None, execute=None, evidence_root):
    state, checks = ready(service, delivery_id, session_id, profile_id)
    if conn is None:
        h.kb.init_db()
        with closing(h.connect(None)) as conn:
            return run(service, delivery_id, session_id=session_id, profile_id=profile_id,
                       conn=conn, execute=execute, evidence_root=evidence_root)
    bridge = NativeBridge(service, conn, session_id=session_id, profile_id=profile_id)
    worker, steps, recoveries, strikes = execute or HermesWorker(), [], [], {}
    store = Path(evidence_root) / 'good' / delivery_id
    claimer = h.new_claimer()
    bridge.abandon(delivery_id)  # recover a run left bound by a crashed build
    for _ in range(max(4, state.plan.budget.calls * 6)):
        state = owned(service, delivery_id, session_id, profile_id)
        if state.activity not in ACTIVE_STEPS:
            break
        action = state.activity
        try:
            state = settle(state, store, service)
            action = state.activity
            tid = bridge.schedule(delivery_id)
            if tid is None:
                raise ValueError('missing runnable native task')
            if action in {'tests', 'checks'}:
                if h.claim_task(conn, tid, claimer=claimer, ttl_seconds=state.plan.budget.seconds) is None:
                    raise ValueError('task owned by another launcher; no worker started')
                bridge.bind_claim(delivery_id, tid, claimer=claimer, worker_session='check-' + uuid.uuid4().hex)
                # tests proves the behavior check before implementing; checks runs everything.
                due = checks if action == 'checks' else tuple(c for c in checks if c.obligation == 'behavior')
                state = bridge.observe_checks(delivery_id, tid, claimer=claimer,
                                              checks=due, evidence_root=Path(evidence_root))
            elif action in {'write_tests', 'implement'}:
                state = observe_implementation(bridge, delivery_id, tid, claimer=claimer, execute=worker)
            else:
                state = observe_review(bridge, delivery_id, tid, claimer=claimer, execute=worker,
                                       evidence_root=Path(evidence_root))
        except WorkerRefused as exc:
            # Not a crash: the next approved actor takes over, or the run waits for the user.
            recoveries.append(f'{bridge._actor(state).profile} declined {action}: {exc}')
            bridge.abandon(delivery_id, claimer, kind='activity_refused')
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
                recoveries.append(f'{action} failed ({reason}); ' + ('retrying' if n < FAILURES else 'giving up'))
                if current.run_id:
                    bridge.abandon(delivery_id, claimer)
                if n >= FAILURES:
                    raise
            else:
                who = bridge._actor(state).profile
                strikes[who] = n = strikes.get(who, 0) + 1
                recoveries.append(f'{who} failed {action} ({reason}); '
                                  + ('retrying' if n < FAILURES else 'handing over'))
                worker.note = (f'Your previous attempt at this step failed: {reason}. Its changes were undone. '
                               'Do the step again and finish with exactly the requested JSON.')
                if n < FAILURES:
                    bridge.abandon(delivery_id, claimer, action='fresh' if n > 1 else '')
                else:
                    worker.note = ''
                    bridge.abandon(delivery_id, claimer, kind='activity_refused')
            time.sleep(min(BACKOFF * n, max(0, state.plan.budget.seconds - (time.time() - state.started_at)) / 4))
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
