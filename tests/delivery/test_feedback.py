from dataclasses import replace
import pytest
from cpipe.delivery.contracts import Outcome
from cpipe.delivery.findings import Finding
from cpipe.delivery.service import apply, green
from .test_service import candidate, event, claim, receipt, step, ready_for_review, reviewed


def test_change_preserves_feedback_reference_and_invalidates_readiness():
    state, _ = reviewed(ready_for_review(candidate()))
    changed, intents = apply(state, event(state, 'feedback_received', action='change',
                                         evidence_ref='feedback/label.json'))
    assert intents[0][1]['feedback_ref'] == 'feedback/label.json'
    assert intents[0][1]['resume'] == state.builder_session
    assert changed.receipts == () and not green(changed)
    assert not changed.readiness_requested
    changed = claim(changed, 'forge', 'builder-session')
    changed, _ = step(changed, 'activity_observed', 'checks', actor_id='forge', result_artifact='tree3')
    changed = claim(changed)
    changed, intents = step(changed, 'activity_observed', receipts=(receipt(changed),))
    assert changed.phase == 'testable' and not intents


@pytest.mark.parametrize('reference', ['', '../private.json', '/private.json'])
def test_change_requires_confined_feedback_reference(reference):
    state = candidate()
    with pytest.raises(ValueError, match='feedback|path'):
        apply(state, event(state, 'feedback_received', action='change', evidence_ref=reference))


def test_builder_cannot_resume_a_different_existing_session():
    state, _ = apply(candidate(), event(candidate(), 'feedback_received', action='change',
                                       evidence_ref='feedback/label.json'))
    with pytest.raises(ValueError, match='resume'):
        claim(state, 'forge', 'different-session')


def test_review_correction_forwards_exact_verdict_evidence_to_builder():
    state = ready_for_review(candidate())
    finding = Finding('', 'behavior', 'missing case', 'high', True, 'open')
    changed, intents = reviewed(state, findings=(finding,),
                               receipts=(receipt(state, 'review', Outcome.FAIL),),
                               evidence_ref='evidence/correction.json')
    assert changed.phase == 'implementing'
    assert intents[0][1]['feedback_ref'] == 'evidence/correction.json'
    assert intents[0][1]['resume'] == 'builder-session'


def test_repeated_finding_diagnosis_keeps_latest_verdict_reference():
    state = ready_for_review(candidate())
    finding = Finding('', 'behavior', 'missing case', 'high', True, 'open')
    state, _ = reviewed(state, findings=(finding,),
                        receipts=(receipt(state, 'review', Outcome.FAIL),))
    state = claim(state, 'forge', 'builder-session')
    state, _ = step(state, 'activity_observed', 'checks', actor_id='forge', result_artifact='tree3')
    state = claim(state)
    state, _ = step(state, 'activity_observed', 'review', receipts=(receipt(state),))
    state = claim(state, 'sentry', 'review-session2')
    state, intents = reviewed(state, findings=(finding,),
                              receipts=(receipt(state, 'review', Outcome.FAIL),),
                              evidence_ref='evidence/repeated.json')
    assert state.phase == 'diagnosis'
    assert state.findings[0].finding.id == 'REV-001'
    assert intents[0][1]['feedback_ref'] == 'evidence/repeated.json'
