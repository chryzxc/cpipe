from dataclasses import replace
import pytest
from cpipe.delivery.contracts import Outcome
from cpipe.delivery.findings import Finding
from cpipe.delivery.service import apply, green, resumable, Verification
from .test_service import candidate, event, claim, receipt, step, ready_for_review, reviewed, passed


def test_change_preserves_feedback_reference_and_invalidates_readiness():
    state, _ = passed(ready_for_review(candidate()))
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


def test_a_finding_no_fix_resolves_hands_over_working_code_and_continue_retries_it():
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
    # Checks pass, so more rounds would only churn: ready for review with the finding left open.
    assert state.phase == 'locally_ready' and not intents
    assert state.findings[0].finding.id == 'REV-001' and state.findings[0].finding.disposition == 'open'
    assert resumable(state)
    state, _ = step(state, 'resumed', 'checks', result_artifact='tree-now')
    assert state.stalls == 0 and state.handover_reviews == state.review_cycles


def test_low_findings_are_advice_and_do_not_send_the_change_back():
    state = ready_for_review(candidate())
    advice = Finding('', 'review', 'name could be clearer', 'low', True, 'open')
    done, intents = passed(state, findings=(advice,))
    assert done.phase == 'locally_ready' and not intents
    assert done.findings[0].finding.disposition == 'open' and not resumable(done)


def test_a_fix_loop_that_trades_findings_stops_after_two_rounds_without_progress():
    state = ready_for_review(candidate())
    for n, cause in enumerate(('first', 'second', 'third')):
        finding = Finding('', 'review', cause + ' bug', 'medium', True, 'open')
        verify = tuple(Verification(r.finding.id, receipt(state, 'review'), 'evidence/v.json')
                       for r in state.findings if r.finding.disposition == 'open')
        state, intents = reviewed(state, findings=(finding,), verifications=verify,
                                  receipts=(receipt(state, 'review', Outcome.FAIL),))
        if n < 2:
            assert state.phase == 'implementing'
            state = claim(state, 'forge', 'builder-session')
            state, _ = step(state, 'activity_observed', 'checks', actor_id='forge', result_artifact=f'tree{n + 3}')
            state = claim(state)
            state, _ = step(state, 'activity_observed', 'review', receipts=(receipt(state),))
            state = claim(state, 'sentry', f'review-session{n + 2}')
    assert state.stalls == 2 and state.phase == 'locally_ready'
