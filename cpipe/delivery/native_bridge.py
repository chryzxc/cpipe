"""Bind v2 engineering activities to Hermes' native queue and run authority.

Only the trusted execution adapter calls this module. It is not a model tool,
worker launcher, approval transport, or sandbox. Worker prose must never be
converted into check receipts here. Hermes still owns claims and scheduling;
stock-dispatch eligibility does not imply confinement or a v2-aware worker.
"""
from __future__ import annotations

import hashlib
import math
import time

from ..runtime import hermes_api as h
from .journal import encoded
from .service import Event, apply

RUNNABLE = frozenset({'tests', 'checks', 'write_tests', 'implement', 'review', 'publish'})
RESULT_KINDS = frozenset({'activity_observed', 'review_received', 'publication_observed'})


class NativeBridge:
    def __init__(self, service, conn, *, session_id, profile_id,
                 notify_target=None, require_notify=False):
        self.service = service
        self.conn = conn
        self.session_id = session_id
        self.profile_id = profile_id
        self.notify_target = notify_target
        self.require_notify = require_notify

    def _state(self, delivery_id):
        state = self.service.read(delivery_id)
        if state is None:
            raise ValueError('unknown delivery')
        e = state.plan.envelope
        if (e.session_id, e.profile_id) != (self.session_id, self.profile_id):
            raise ValueError('delivery session/profile mismatch')
        return state

    @staticmethod
    def _key(state):
        # Includes profile/session so two initiating profiles cannot alias cards.
        e = state.plan.envelope
        material = encoded([e.profile_id, e.session_id, state.plan.digest(), state.active_intent])
        return 'cpipe-v2:' + hashlib.sha256(material.encode()).hexdigest()

    @staticmethod
    def _actor(state):
        role = 'reviewer' if state.activity == 'review' else 'builder'
        return next(a for a in state.plan.actors if a.role == role)

    @staticmethod
    def _lane(profile):
        # A non-profile assignee: Hermes' dispatcher never spawns it, so only
        # cpipe claims these cards (Hermes stores assignees lowercased).
        return 'cp:' + profile.lower()

    def _packet(self, state):
        return {'schema': 1, 'delivery_id': state.plan.envelope.delivery_id,
                'plan_digest': state.plan.digest(), 'intent_id': state.active_intent,
                'action': state.activity, 'artifact': state.artifact,
                'plan': state.plan.to_dict()}

    def _task(self, state, task_id):
        task = h.get_task(self.conn, task_id)
        actor = self._actor(state)
        if (not task or task.created_by != 'cpipe'
                or task.idempotency_key != self._key(state)
                or task.session_id != self.session_id or task.assignee != self._lane(actor.profile)
                or task.model_override != actor.model or task.provider_override != actor.provider
                or task.workspace_kind != 'dir' or task.workspace_path != state.plan.worktree
                or task.body != self._body(state)):
            raise ValueError('native task does not match frozen activity')
        return task

    def _body(self, state):
        return 'cpipe v2 activity contract (trusted execution adapter required):\n' + encoded(self._packet(state))

    def schedule(self, delivery_id):
        state = self._state(delivery_id)
        if not state.approved or state.activity not in RUNNABLE or state.run_id:
            return None
        now = int(time.time())
        remaining = min(state.plan.expires_at - now,
                        state.plan.budget.seconds - (now - state.started_at))
        if remaining <= 0:
            raise ValueError('execution authority expired or budget exhausted')
        if self.require_notify and not self.notify_target:
            raise RuntimeError('native wake subscription is required before scheduling')
        actor = self._actor(state)
        if actor.engine != 'hermes':
            raise ValueError('native bridge requires a Hermes actor')
        # Serialize native idempotency lookup/create. Hermes create_task supports
        # nesting, but its standalone pre-check is otherwise subject to a race.
        with h.kb.write_txn(self.conn):
            tid = h.kb.create_task(self.conn, title=f'cpipe v2: {state.activity}',
                body=self._body(state), assignee=self._lane(actor.profile), created_by='cpipe',
                workspace_kind='dir', workspace_path=state.plan.worktree,
                project_id='', session_id=self.session_id,
                idempotency_key=self._key(state), max_runtime_seconds=math.ceil(remaining),
                max_retries=state.plan.transient_retries,
                model_override=actor.model, provider_override=actor.provider,
                initial_status='blocked', completion_contract='local-only')
        task = self._task(state, tid)
        # Commit and read back subscriptions before making a card dispatchable.
        # If this fails the card stays blocked; rerunning schedule repairs it.
        if self.notify_target:
            from hermes_cli import kanban_db_notify as notify
            target = {**self.notify_target, 'delivery_mode': 'notify+wake'}
            notify.add_notify_sub(self.conn, task_id=tid, **target)
            subs = notify.list_notify_subs(self.conn, tid)
            expected = {**target, 'thread_id': target.get('thread_id') or ''}
            if not any(all(s.get(k) == v for k, v in expected.items()) for s in subs):
                raise RuntimeError('native wake subscription readback failed')
        if task.status == 'blocked':
            # No old claim exists for a newly scheduled activity. Do not resume
            # an existing blocked/failed run by accident on an outbox replay.
            if task.current_run_id is not None:
                raise ValueError('native run requires explicit recovery')
            if not h.kb.unblock_task(self.conn, tid):
                raise RuntimeError('native task could not be queued')
        fresh = self._task(state, tid)
        if fresh.status not in {'ready', 'running'}:
            raise ValueError('native activity is not queueable')
        self.service.journal.ack(state.active_intent)
        return tid

    def _owned(self, state, task_id, claimer):
        task = self._task(state, task_id)
        now = int(time.time())
        if (task.status != 'running' or task.claim_lock != claimer
                or not task.current_run_id or not task.claim_expires or task.claim_expires <= now):
            raise ValueError('native ownership missing or expired')
        return task

    def _event(self, state, kind, **fields):
        e = state.plan.envelope
        return Event(key=f'{state.active_intent}:{state.run_id}:{kind}', kind=kind,
            delivery_id=e.delivery_id, plan_digest=state.plan.digest(), artifact=state.artifact,
            expected_version=state.version, now=max(state.last_at, int(time.time())),
            session_id=e.session_id, profile_id=e.profile_id,
            activity_id=state.active_intent, run_id=fields.pop('run_id', state.run_id),
            worker_session=fields.pop('worker_session', state.worker_session), **fields)

    def bind_claim(self, delivery_id, task_id, *, claimer, worker_session):
        state = self._state(delivery_id)
        task = self._owned(state, task_id, claimer)
        run_id = f'{task.id}:{task.current_run_id}'
        if state.run_id:
            if state.run_id != run_id or state.worker_session != worker_session:
                raise ValueError('native run/session differs from bound claim')
            return state
        event = self._event(state, 'activity_claimed', run_id=run_id,
                            worker_session=worker_session, actor_id=self._actor(state).profile)
        return self.service.accept(event)[0]

    def result_event(self, delivery_id, task_id, *, claimer, kind, **result):
        if kind not in RESULT_KINDS:
            raise ValueError('unsupported native result kind')
        # Binding fields cannot be overridden by an execution result.
        if set(result) & {'key', 'delivery_id', 'plan_digest', 'artifact', 'expected_version',
                          'now', 'session_id', 'profile_id', 'activity_id', 'run_id',
                          'actor_id', 'worker_session', 'plan'}:
            raise ValueError('result cannot supply authority or binding fields')
        state = self._state(delivery_id)
        task = self._owned(state, task_id, claimer)
        if state.run_id != f'{task.id}:{task.current_run_id}':
            raise ValueError('native run was not bound to this activity')
        return self._event(state, kind, actor_id=self._actor(state).profile, **result)

    def observe_checks(self, delivery_id, task_id, *, claimer, checks, evidence_root):
        """Execute frozen checks and reconcile observations, never worker prose."""
        from .check_runner import observe_pytest
        state = self._state(delivery_id)
        task = self._owned(state, task_id, claimer)
        if state.activity not in {'tests', 'checks'} or state.run_id != f'{task.id}:{task.current_run_id}':
            raise ValueError('native check activity must be bound before execution')
        expected = {c.obligation for c in state.plan.checks if c.obligation != 'review'}
        if state.activity == 'tests':
            expected = {'behavior'}
        if len(checks) != len(expected) or {c.obligation for c in checks} != expected:
            raise ValueError('check adapter coverage differs from frozen activity')
        receipts = []
        for spec in checks:
            if self._state(delivery_id) != state:
                raise ValueError('engineering authority changed during checks')
            self._owned(state, task_id, claimer)
            receipts.append(observe_pytest(state, spec, evidence_root=evidence_root))
        if self._state(delivery_id) != state:
            raise ValueError('engineering authority changed during checks')
        self._owned(state, task_id, claimer)
        event = self.result_event(delivery_id, task_id, claimer=claimer,
                                  kind='activity_observed', receipts=tuple(receipts))
        return self.finish(event, task_id, claimer=claimer,
                           summary='Observed frozen checks: ' + ', '.join(
                               f'{r.obligation}={r.outcome.value}' for r in receipts))

    def finish(self, event, task_id, *, claimer, summary):
        """Close a native run then reconcile its exact read-back fact.

        A crash between commits is repaired by replaying the same event. Native
        metadata records the event, not a second approval authority. Cancellations
        or newer engineering versions still fence late results out of the journal.
        """
        if type(event) is not Event or event.kind not in RESULT_KINDS:
            raise ValueError('unsupported native result event')
        state = self._state(event.delivery_id)
        if not summary or not summary.strip():
            raise ValueError('observed result summary is required')
        recorded = self.service.recorded(event)
        task = h.get_task(self.conn, task_id)
        run = h.kb.latest_run(self.conn, task_id)
        if not task or task.assignee != self._lane(event.actor_id):
            raise ValueError('native result actor mismatch')
        native_recorded = bool(task and run and task.status == 'done'
            and run.outcome == 'completed' and run.claim_lock == claimer
            and f'{task_id}:{run.id}' == event.run_id
            and (run.metadata or {}).get('cpipe_v2_event') == event.to_dict())
        if recorded:
            if not native_recorded:
                raise ValueError('recorded engineering result lacks matching native run')
            return self.service.read(event.delivery_id)
        # Pure validation before committing either result. Never complete a card
        # on an invalid receipt, wrong actor, stale artifact or mismatched session.
        apply(state, event)
        if not native_recorded:
            task = self._owned(state, task_id, claimer)
            if state.run_id != event.run_id or event.run_id != f'{task.id}:{task.current_run_id}':
                raise ValueError('native result run mismatch')
            if not h.complete(self.conn, task_id, run_id=task.current_run_id, summary=summary,
                              metadata={'cpipe_v2_event': event.to_dict()}):
                raise RuntimeError('native completion was rejected')
            task = h.get_task(self.conn, task_id)
            run = h.kb.latest_run(self.conn, task_id)
            if (not task or task.status != 'done' or not run or run.outcome != 'completed'
                    or f'{task_id}:{run.id}' != event.run_id
                    or (run.metadata or {}).get('cpipe_v2_event') != event.to_dict()):
                raise RuntimeError('native completion readback failed')
        return self.service.accept(event)[0]
