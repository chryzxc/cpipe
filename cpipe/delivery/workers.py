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


def _answer(payload, open_ids):
    """The findings a builder disputes, from its answer. The edits and the checks are the result, so an
    answer that is not the asked-for JSON still counts as done."""
    disputed = payload.get('disputed') if type(payload) is dict else None
    return {d['id']: d['reason'].strip() for d in disputed if type(d) is dict and d.get('id') in open_ids
            and type(d.get('reason')) is str and d['reason'].strip()} if type(disputed) is list else {}


def _scope(before, after, plan, allow_same=False):
    old = {name: (digest, mode) for name, digest, mode in before.files}
    new = {name: (digest, mode) for name, digest, mode in after.files}
    changed = {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)}
    def within(name, paths):
        return any(name == path or name.startswith(path + '/') for path in paths)
    # write_paths guide the builder; the review sees every changed file. Only forbidden paths are refused.
    if any(within(name, plan.forbidden_paths) for name in changed):
        raise ValueError('worker changed a forbidden path')
    if before.digest == after.digest and not allow_same:
        raise ValueError('implementation lacks an observed successor artifact')
    return changed


def observe_implementation(bridge, delivery_id, task_id, *, claimer, execute):
    """Claim one native activity; only actual in-scope edits advance checks.

    execute must be a trusted adapter, not model-supplied executable code. Its
    on_session callback binds the host-observed child ID; heartbeat renews and
    revalidates the native lease throughout the bounded worker invocation.
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
        disputes = _answer(result.payload, {r.finding.id for r in state.findings if blocks(r.finding)})
        result = None
        after = validate_worktree(state.plan)
        if after.head != before.head:  # it committed: keep the edits, uncommitted, for the checks and review
            from .hermes_worker import reattach
            reattach(state.plan)
            after = validate_worktree(state.plan)
        # Disputing the findings is a valid answer that changes nothing, unless checks still fail.
        _scope(before, after, state.plan, allow_same=bool(disputes) and green(state, include_review=False))
        if disputes:
            from .review import redacted
            if len(DISPUTES) > 500:
                DISPUTES.clear()
            DISPUTES.update({delivery_id + ':' + i: redacted(r) for i, r in disputes.items()})
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
