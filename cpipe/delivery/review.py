"""Observe independent Hermes judgments against an unchanged candidate.

A review receipt observes an independent verdict, not test execution or a proof
of correctness. Raw model prose is never saved; stable finding identities hash
root-cause descriptions, conservatively retaining unresolved findings on replay.
The builder must re-inspect acceptance and review axes rather than infer a fix
from a hash. Review is unsandboxed; mutation checks are detection, not isolation.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import os
import uuid

from .artifact import validate_worktree
from .contracts import Outcome, Receipt
from .findings import Finding, blocks
from .journal import encoded
from .schema import CheckIdentity
from .service import Verification
from .workers import WorkerResult, _remaining, _step_seconds, record_usage, refused
from ..runtime import hermes_api as h

REVIEW_AXES = ('goal_alignment', 'correctness', 'regressions', 'integration',
               'breaking_behavior', 'maintainability', 'security', 'accessibility',
               'operability', 'evidence_quality', 'simplicity')


def _hash(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def review_identity():
    return CheckIdentity('review', _hash(REVIEW_AXES),
                         _hash('hermes-independent-observed-json-v1'),
                         _hash('unchanged-full-range-review-v1'))


def _receipt(state, outcome):
    check = review_identity()
    return Receipt('review', state.artifact, state.plan.envelope.scope,
                   check.inputs, check.environment, check.command, outcome, True)


# Redacted finding text by hashed root cause, for the builder's next turn only.
# Process memory, never journal or evidence: after a restart the builder falls
# back to re-inspecting the hash-only findings.
NOTES = {}


def remember(payload):
    from agent.redact import redact_sensitive_text
    from ..runtime.envpolicy import Redactor
    redact = Redactor(os.environ)
    if len(NOTES) > 500:
        NOTES.clear()
    for value in payload['findings']:
        NOTES['cause-' + _hash(value['root_cause'])] = redact(redact_sensitive_text(value['root_cause']))[:1000]


def _parse(state, payload):
    expected = {'status', 'artifact', 'reviewed_from', 'coverage', 'findings', 'verified_ids'}
    if (type(payload) is not dict or set(payload) != expected
            or payload['artifact'] != state.artifact
            or payload['reviewed_from'] != state.plan.base_commit
            or type(payload['coverage']) is not list
            or len(payload['coverage']) != len(REVIEW_AXES)
            or any(type(x) is not str for x in payload['coverage'])
            or set(payload['coverage']) != set(REVIEW_AXES)
            or payload['status'] not in {'approved', 'changes_requested'}
            or type(payload['findings']) is not list or len(payload['findings']) > 100
            or type(payload['verified_ids']) is not list):
        raise ValueError('review lacks exact full-range structured coverage')
    findings = []
    for value in payload['findings']:
        if (type(value) is not dict
                or set(value) != {'id', 'criterion', 'root_cause', 'severity', 'in_scope', 'disposition'}
                or value['id'] != '' or value['criterion'] != 'review'
                or type(value['root_cause']) is not str or not value['root_cause'].strip()
                or len(value['root_cause']) > 4000 or value['disposition'] != 'open'):
            raise ValueError('invalid structured finding')
        safe = {**value, 'root_cause': 'cause-' + _hash(value['root_cause'])}
        findings.append(Finding(**safe))
    if len({f.root_cause for f in findings}) != len(findings):
        raise ValueError('duplicate root-cause finding')
    ids = payload['verified_ids']
    if (any(type(x) is not str for x in ids) or len(set(ids)) != len(ids)
            or not set(ids) <= {r.finding.id for r in state.findings}):
        raise ValueError('unknown or duplicate prior-ID verification')
    signatures = {f.root_cause for f in findings}
    if any(r.finding.id in ids and r.finding.root_cause in signatures for r in state.findings):
        raise ValueError('contradictory verification and recurrence')
    # The findings decide, not the status word: a reviewer that says changes_requested over
    # advice only, or approved over a serious finding, is not a reason to fail the step.
    failed = (any(blocks(r.finding) and r.finding.id not in ids for r in state.findings)
              or any(blocks(f) for f in findings))
    return tuple(findings), tuple(ids), Outcome.FAIL if failed else Outcome.PASS


def observe_review(bridge, delivery_id, task_id, *, claimer, execute, evidence_root):
    state = bridge._state(delivery_id)
    if not state.approved or state.activity != 'review' or state.run_id:
        raise ValueError('review must be an approved unclaimed activity')
    if review_identity() not in state.plan.checks:
        raise ValueError('review adapter differs from frozen review policy')
    before = validate_worktree(state.plan)
    if before.digest != state.artifact:
        raise ValueError('artifact differs from pending review')
    root = Path(evidence_root).absolute()
    if root.resolve() != root or root.is_relative_to(Path(state.plan.worktree)):
        raise ValueError('review evidence must be outside source in a non-symlink directory')
    timeout = _step_seconds(state)
    bridge._task(state, task_id)
    if not h.take(bridge.conn, task_id, claimer=claimer,
                        ttl_seconds=math.ceil(timeout) + 5):
        raise ValueError('native review already claimed')
    bound = None

    def on_session(sid):
        nonlocal bound
        if bound is not None or sid == state.builder_session:
            raise ValueError('review requires a distinct new observed session')
        bound = bridge.bind_claim(delivery_id, task_id, claimer=claimer, worker_session=sid)

    def heartbeat():
        expected = bound or state
        if bridge._state(delivery_id) != expected:
            raise ValueError('engineering authority changed during review')
        bridge._owned(expected, task_id, claimer)
        if not h.heartbeat_claim(bridge.conn, task_id, claimer=claimer,
                                 ttl_seconds=math.ceil(_remaining(expected)) + 5):
            raise ValueError('native review ownership could not be renewed')

    try:
        result = execute(state, on_session=on_session, heartbeat=heartbeat,
                         timeout_seconds=timeout)
        heartbeat()
        if (type(result) is not WorkerResult or bound is None
                or result.session_id != bound.worker_session):
            raise ValueError('review result does not match observed native session')
        if validate_worktree(state.plan) != before:
            raise ValueError('artifact changed during read-only review')
        record_usage(bridge.service, state, 'review', result)
        refused(result)
        findings, ids, outcome = _parse(state, result.payload)
        remember(result.payload)
        # Arbitrary text/diagnostics/credential values never enter durable evidence.
        result = None
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = f'{uuid.uuid4().hex}.json'
        ref = 'review/' + name
        receipt = _receipt(state, outcome)
        evidence = dict(schema=1, artifact=before.digest, session_id=bound.worker_session,
                        coverage=list(REVIEW_AXES), receipt=receipt.to_dict(),
                        findings=[f.to_dict() for f in findings], verified_ids=list(ids),
                        details='[REDACTED]')
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(encoded(evidence))
        verifications = tuple(Verification(i, _receipt(state, Outcome.PASS), ref) for i in ids)
        event = bridge.result_event(delivery_id, task_id, claimer=claimer, kind='review_received',
            receipts=(receipt,), findings=findings, verifications=verifications,
            verdict=_hash(evidence), reviewed_from=state.plan.base_commit,
            reviewed_to=state.artifact, evidence_ref=ref)
        return bridge.finish(event, task_id, claimer=claimer,
                             summary='Observed independent review: ' + outcome.value)
    except Exception:
        current = h.get_task(bridge.conn, task_id)
        if (current and current.status == 'running' and current.claim_lock == claimer
                and current.current_run_id is not None):
            h.block(bridge.conn, task_id, run_id=current.current_run_id,
                    reason='review observation unavailable; artifact preserved', kind='needs_input')
        raise
