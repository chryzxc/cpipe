"""Trusted v2 worker observations using native claims and Git readback.

Prompts and post-run scope checks are not isolation. Local Hermes execution is
unsandboxed; a successful worker status is never test evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time

from .artifact import validate_worktree
from .service import State
from ..runtime import hermes_api as h


@dataclass(frozen=True)
class WorkerResult:
    session_id: str
    payload: dict


def _remaining(state):
    now = time.time()
    remaining = min(state.plan.expires_at - now,
                    state.plan.budget.seconds - (now - state.started_at))
    if remaining <= 0:
        raise ValueError('worker authority expired or budget exhausted')
    return remaining


def _scope(before, after, plan):
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
    if before.digest == after.digest:
        raise ValueError('implementation lacks an observed successor artifact')


def observe_implementation(bridge, delivery_id, task_id, *, claimer, execute):
    """Claim one native activity; only actual in-scope edits advance checks.

    execute must be a trusted adapter, not model-supplied executable code. Its
    on_session callback binds the host-observed child ID; heartbeat renews and
    revalidates the native lease throughout the bounded worker invocation.
    """
    state = bridge._state(delivery_id)
    if not state.approved or state.activity != 'implement' or state.run_id:
        raise ValueError('implementation must be an approved unclaimed activity')
    before = validate_worktree(state.plan)
    if before.digest != state.artifact:
        raise ValueError('artifact differs from pending implementation')
    timeout = _remaining(state)
    task = bridge._task(state, task_id)
    if not h.claim_task(bridge.conn, task_id, claimer=claimer,
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
        if result.payload != {'status': 'done'}:
            raise ValueError('worker did not return a valid implementation result')
        after = validate_worktree(state.plan)
        _scope(before, after, state.plan)
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
                    reason='v2 implementation observation unavailable; local edits preserved',
                    kind='needs_input')
        raise
