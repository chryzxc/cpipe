"""Trusted worker observations using native claims and Git readback.

Prompts and post-run scope checks are not isolation. Local Hermes execution is
unsandboxed; a successful worker status is never test evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import time

from .artifact import validate_worktree
from .journal import encoded
from .service import State, deadline, green
from ..runtime import hermes_api as h


@dataclass(frozen=True)
class WorkerResult:
    session_id: str
    payload: dict
    tokens: dict | None = None  # host-reported usage: input/output/total/cache_read/cache_write


class WorkerRefused(ValueError):
    """The worker declined the step (e.g. its profile forbids edits); retrying it is pointless."""


def refused(result):
    """Raise WorkerRefused for a {"status":"blocked","reason":...} answer."""
    payload = getattr(result, 'payload', None)
    if type(payload) is dict and payload.get('status') == 'blocked':
        raise WorkerRefused(str(payload.get('reason') or 'no reason given')[:300])


def record_usage(service, state, activity, result):
    """Keep per-step token usage beside the journal; it never grants authority."""
    tokens = getattr(result, 'tokens', None)
    if not tokens:
        return
    key = f'usage:{state.plan.envelope.delivery_id}:{state.version}:{activity}'
    service.journal.db.execute('INSERT OR IGNORE INTO intent(id,payload,acknowledged) VALUES (?,?,1)',
                               (key, encoded(dict(activity=activity, **tokens))))


def usage(service, delivery_id):
    rows = service.journal.db.execute("SELECT payload FROM intent WHERE id LIKE ?",
                                      (f'usage:{delivery_id}:%',)).fetchall()
    steps = [json.loads(r[0]) for r in rows]
    total = {k: sum(s.get(k, 0) for s in steps) for k in ('input', 'output', 'total', 'cache_read', 'cache_write')}
    return dict(steps=len(steps), **total)


def _step_seconds(state):
    """One worker step's wall clock: a hung worker cannot spend the whole budget."""
    return min(_remaining(state), max(600, state.plan.budget.seconds / 3))


def _remaining(state):
    now = time.time()
    remaining = deadline(state) - now
    if remaining <= 0:
        raise ValueError('worker authority expired or budget exhausted')
    return remaining


# Builder reasons for leaving a finding unchanged, by finding ID, for the next review and the
# final message only. Process memory, never journal or evidence, like review.NOTES.
DISPUTES = {}


def _answer(state, payload, open_ids):
    """(tests, disputes) from {"status":"done"} plus, when fixing review findings, the regression
    tests it added and the findings it disputes."""
    if (type(payload) is not dict or payload.get('status') != 'done'
            or not set(payload) <= {'status', 'tests', 'disputed'}
            or (set(payload) != {'status'} and state.activity != 'implement')):
        raise ValueError('worker did not return a valid implementation result')
    tests, disputed = payload.get('tests', []), payload.get('disputed', [])
    if (type(tests) is not list or any(type(t) is not str or not t for t in tests)
            or type(disputed) is not list or len(disputed) > 100
            or any(type(d) is not dict or set(d) != {'id', 'reason'} or d['id'] not in open_ids
                   or type(d['reason']) is not str or not d['reason'].strip() for d in disputed)):
        raise ValueError('worker listed unknown findings or invalid tests')
    return set(tests), {d['id']: d['reason'] for d in disputed}


def _scope(before, after, plan, allow_same=False):
    if before.head != after.head:
        raise ValueError('worker cannot commit before frozen checks and review')
    old = {name: (digest, mode) for name, digest, mode in before.files}
    new = {name: (digest, mode) for name, digest, mode in after.files}
    changed = {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)}
    def within(name, paths):
        return any(name == path or name.startswith(path + '/') for path in paths)
    if any(not within(name, plan.write_paths) or within(name, plan.forbidden_paths)
           for name in changed):
        raise ValueError('worker changed files outside approved write scope')
    if before.digest == after.digest and not allow_same:
        raise ValueError('implementation lacks an observed successor artifact')
    return changed


def observe_implementation(bridge, delivery_id, task_id, *, claimer, execute, prove=None):
    """Claim one native activity; only actual in-scope edits advance checks.

    execute must be a trusted adapter, not model-supplied executable code. Its
    on_session callback binds the host-observed child ID; heartbeat renews and
    revalidates the native lease throughout the bounded worker invocation.
    prove(tests) must confirm that the listed regression tests fail without the step's other edits.
    """
    state = bridge._state(delivery_id)
    if not state.approved or state.activity not in {'write_tests', 'implement'} or state.run_id:
        raise ValueError('implementation must be an approved unclaimed activity')
    before = validate_worktree(state.plan)
    if before.digest != state.artifact:
        raise ValueError('artifact differs from pending implementation')
    timeout = _step_seconds(state)
    task = bridge._task(state, task_id)
    if not h.take(bridge.conn, task_id, claimer=claimer,
                        ttl_seconds=math.ceil(timeout) + 5):
        raise ValueError('native activity already claimed')
    bound = None

    def on_session(session_id):
        nonlocal bound
        if bound is not None:
            raise ValueError('duplicate worker session initialization')
        bound = bridge.bind_claim(delivery_id, task_id, claimer=claimer, worker_session=session_id)

    def heartbeat():
        current = bridge._state(delivery_id)
        expected = bound or state
        if current != expected:
            raise ValueError('engineering authority changed during worker execution')
        bridge._owned(expected, task_id, claimer)
        _remaining(expected)
        if not h.heartbeat_claim(bridge.conn, task_id, claimer=claimer,
                                 ttl_seconds=math.ceil(_remaining(expected)) + 5):
            raise ValueError('native ownership could not be renewed')

    try:
        result = execute(state, on_session=on_session, heartbeat=heartbeat,
                         timeout_seconds=timeout)
        heartbeat()
        if (type(result) is not WorkerResult or bound is None
                or result.session_id != bound.worker_session):
            raise ValueError('worker result session does not match native claim')
        record_usage(bridge.service, state, state.activity, result)
        refused(result)
        from .findings import blocks
        tests, disputes = _answer(state, result.payload, {r.finding.id for r in state.findings if blocks(r.finding)})
        result = None
        after = validate_worktree(state.plan)
        # Disputing the findings is a valid answer that changes nothing, unless checks still fail.
        changed = _scope(before, after, state.plan, allow_same=bool(disputes) and green(state, include_review=False))
        if not tests <= changed:
            raise ValueError('listed regression tests were not changed in this step')
        if tests and prove is not None and not prove(tests):
            raise ValueError('the listed regression tests pass without your fix, so they do not reproduce the '
                             'findings: make each fail on the code before the fix, or dispute the finding')
        if disputes:
            from .review import redacted
            if len(DISPUTES) > 500:
                DISPUTES.clear()
            DISPUTES.update({i: redacted(r) for i, r in disputes.items()})
        event = bridge.result_event(delivery_id, task_id, claimer=claimer,
            kind='activity_observed', result_artifact=after.digest)
        return bridge.finish(event, task_id, claimer=claimer,
                              summary='Observed in-scope implementation; frozen checks pending')
    except Exception:
        # Preserve the artifact and claimed engineering fact for explicit
        # recovery. Never let a failed worker masquerade as a completed card.
        current = h.get_task(bridge.conn, task_id)
        if (current and current.status == 'running' and current.claim_lock == claimer
                and current.current_run_id is not None):
            h.block(bridge.conn, task_id, run_id=current.current_run_id,
                    reason='implementation observation unavailable; local edits preserved',
                    kind='needs_input')
        raise
