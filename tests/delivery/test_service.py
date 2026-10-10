from dataclasses import replace
import json
import pytest
from cpipe.delivery.contracts import Outcome, Receipt
from cpipe.delivery.findings import Finding
from cpipe.delivery.schema import ExecutionPlan
from cpipe.delivery.service import Event, State, Verification, apply
from .test_execution_plan import plan


def event(state, kind, **changes):
    fields = dict(key=f"e{state.version + 1}", kind=kind,
                  delivery_id=state.plan.envelope.delivery_id, plan_digest=state.plan.digest(),
                  artifact=state.artifact, expected_version=state.version, now=state.last_at + 1,
                  session_id=state.plan.envelope.session_id, profile_id=state.plan.envelope.profile_id,
                  activity_id=state.active_intent, run_id=state.run_id,
                  worker_session=state.worker_session)
    fields.update(changes)
    return Event(**fields)


def start(**changes):
    p = plan(**changes)
    e = Event("start", "proposal_created", "d1", p.digest(), "tree1", 0, 100, plan=p,
              session_id=p.envelope.session_id, profile_id=p.envelope.profile_id)
    state, intents = apply(None, e)
    assert len(intents) == 1 and intents[0][1]["action"] == "approve_envelope"
    return state


def step(state, kind, expected_action=None, **changes):
    next_state, intents = apply(state, event(state, kind, **changes))
    assert next_state.version == state.version + 1
    assert len(intents) <= 1
    if expected_action is not None:
        assert len(intents) == 1 and intents[0][1]["action"] == expected_action
    return next_state, intents


def claim(state, actor_id="controller", worker_session=""):
    return step(state, "activity_claimed", run_id=f"run{state.version}",
                actor_id=actor_id, worker_session=worker_session)[0]


def receipt(state, name="behavior", outcome=Outcome.PASS, **changes):
    check = next(c for c in state.plan.checks if c.obligation == name)
    value = Receipt(name, state.artifact, state.plan.envelope.scope,
                    check.inputs, check.environment, check.command, outcome, True)
    return replace(value, **changes)


def candidate(**changes):
    state = start(**changes)
    state, _ = step(state, "envelope_approved", "tests")
    state = claim(state)
    state, _ = step(state, "activity_observed", "implement", receipts=(receipt(state, outcome=Outcome.FAIL),))
    state = claim(state, "forge", "builder-session")
    state, _ = step(state, "activity_observed", "checks", actor_id="forge", result_artifact="tree2")
    state = claim(state)
    state, intents = step(state, "activity_observed", receipts=(receipt(state),))
    assert state.phase == "testable" and not intents
    return state


def reviewed(state, **changes):
    fields = dict(actor_id="sentry", receipts=(receipt(state, "review"),), verdict="v1",
                  reviewed_from=state.plan.base_commit, reviewed_to=state.artifact,
                  evidence_ref="evidence/review.json")
    fields.update(changes)
    return step(state, "review_received", **fields)


def ready_for_review(state):
    state, intents = step(state, "feedback_received", "review", action="ready")
    assert intents[0][1]["resume"] == ""
    return claim(state, "sentry", "review-session")


def test_approval_tests_implementation_checks_candidate_then_independent_readiness():
    state = candidate()
    assert not any(r.obligation == "review" for r in state.receipts)
    state = ready_for_review(state)
    state, intents = reviewed(state)
    assert state.phase == "locally_ready" and not intents
    assert State.from_dict(json.loads(json.dumps(state.to_dict()))) == state


def test_in_scope_feedback_reuses_builder_without_planning_or_extra_approval():
    state = candidate()
    next_state, intents = step(state, "feedback_received", "implement", action="change",
                               evidence_ref="feedback/label.json")
    assert next_state.plan == state.plan
    assert next_state.approved
    assert intents[0][1]["resume"] == "builder-session"
    assert next_state.phase == "implementing"


def test_material_scope_change_needs_new_frozen_native_approval():
    state = candidate()
    changed = replace(state.plan, write_paths=("src/b.py",))
    next_state, intents = step(state, "feedback_received", "approve_envelope", plan=changed)
    assert not next_state.approved and next_state.receipts == ()
    assert intents[0][1]["plan_digest"] == changed.digest()
    with pytest.raises(ValueError, match="stale"):
        apply(next_state, replace(event(next_state, "envelope_approved"), plan_digest=state.plan.digest()))


def test_readiness_does_not_authorize_publication_and_answers_cannot_grant_it():
    state, _ = reviewed(ready_for_review(candidate()))
    waiting, intents = step(state, "feedback_received", "approve_publication", action="publish")
    assert waiting.phase == "waiting"
    assert waiting.plan.envelope.capabilities == frozenset({"local"})
    answered, intents = step(waiting, "clarification_answered")
    assert not intents and answered.phase == "waiting"
    assert answered.plan == waiting.plan


@pytest.mark.parametrize("field,value", [
    ("delivery_id", "another"), ("plan_digest", "old"), ("artifact", "old"),
    ("expected_version", 0), ("now", 1), ("session_id", "foreign"), ("profile_id", "foreign"),
])
def test_stale_or_cross_delivery_events_are_rejected(field, value):
    state = start()
    with pytest.raises(ValueError, match="stale"):
        apply(state, replace(event(state, "envelope_approved"), **{field: value}))


def test_unclaimed_wrong_run_and_cancelled_results_cannot_continue():
    state, _ = step(start(), "envelope_approved", "tests")
    with pytest.raises(ValueError, match="claimed"):
        apply(state, event(state, "activity_observed", receipts=(receipt(state),)))
    state = claim(state)
    with pytest.raises(ValueError, match="claimed"):
        apply(state, event(state, "activity_observed", run_id="late-run", receipts=(receipt(state),)))
    cancelled, intents = step(state, "cancelled")
    assert cancelled.phase == "cancelled" and not intents
    with pytest.raises(ValueError, match="terminal"):
        apply(cancelled, event(cancelled, "activity_observed", receipts=(receipt(cancelled),)))


@pytest.mark.parametrize("changes", [
    {"artifact": "old"}, {"scope": "old"}, {"inputs": "old"},
    {"environment": "old"}, {"command": "old"}, {"observed": False},
])
def test_checks_require_current_artifact_and_full_configuration(changes):
    state, _ = step(start(), "envelope_approved", "tests")
    state = claim(state)
    with pytest.raises(ValueError, match="receipt"):
        apply(state, event(state, "activity_observed", receipts=(replace(receipt(state), **changes),)))


def test_missing_observation_or_setup_failure_never_creates_red_or_green():
    state, _ = step(start(), "envelope_approved", "tests")
    state = claim(state)
    with pytest.raises(ValueError, match="observations"):
        apply(state, event(state, "activity_observed"))
    unavailable, intents = step(state, "activity_observed", "diagnose",
                                receipts=(receipt(state, outcome=Outcome.UNAVAILABLE),))
    assert unavailable.phase == "waiting"
    assert unavailable.receipts[-1].outcome is Outcome.UNAVAILABLE


def test_reviewer_cannot_use_builder_session_or_old_partial_range():
    state, _ = step(candidate(), "feedback_received", "review", action="ready")
    with pytest.raises(ValueError, match="builder session"):
        claim(state, "sentry", "builder-session")
    state = claim(state, "sentry", "review-session")
    with pytest.raises(ValueError, match="coverage"):
        reviewed(state, reviewed_from="tree1")


def test_serious_finding_keeps_stable_id_until_successor_verification():
    state = ready_for_review(candidate())
    finding = Finding("", "behavior", "missing boundary case", "high", True, "open")
    state, intents = reviewed(state, receipts=(receipt(state, "review", Outcome.FAIL),), findings=(finding,))
    assert state.phase == "implementing" and intents[0][1]["action"] == "implement"
    assert state.findings[0].finding.id == "REV-001"
    state = claim(state, "forge", "builder-session")
    state, _ = step(state, "activity_observed", "checks", actor_id="forge", result_artifact="tree3")
    state = claim(state)
    state, _ = step(state, "activity_observed", "review", receipts=(receipt(state),))
    state = claim(state, "sentry", "review-session2")
    # Merely omitting an earlier finding cannot close it.
    blocked, intents = reviewed(state)
    assert blocked.findings[0].finding.disposition == "open"
    assert blocked.phase == "diagnosis" and intents[0][1]["action"] == "diagnose"
    verified = Verification("REV-001", receipt(state), "evidence/regression.json")
    done, intents = reviewed(state, verifications=(verified,))
    assert done.phase == "locally_ready" and not intents
    assert done.findings[0].finding.disposition == "verified"
    assert len(done.findings[0].history) == 1


def test_budget_and_expiry_stop_without_new_worker():
    state = candidate()
    for now in (state.plan.expires_at, state.started_at + 300):
        stopped, intents = step(state, "feedback_received", now=now, action="change")
        assert stopped.phase == "stopped" and not intents
    exhausted = replace(state, calls_used=state.plan.budget.calls)
    stopped, intents = step(exhausted, "feedback_received", action="change",
                            evidence_ref="feedback/change.json")
    assert stopped.phase == "stopped" and not intents


def test_execution_budget_starts_at_approval_not_at_freeze():
    state = start()
    approved, _ = step(state, "envelope_approved", "tests", now=state.started_at + 200)
    assert approved.started_at == state.started_at + 200
    claimed, _ = step(approved, "activity_claimed", now=approved.started_at + 250, run_id="run-1")
    assert claimed.phase != "stopped"


def test_illegal_events_do_not_mutate_existing_snapshot():
    state = start()
    old = state.to_dict()
    with pytest.raises(ValueError):
        apply(state, event(state, "publication_observed"))
    assert state.to_dict() == old
    with pytest.raises(ValueError):
        step(state, "feedback_received", action="change")


def test_new_finding_cannot_arrive_preverified_or_predismissed():
    state = ready_for_review(candidate())
    for status in ("verified", "dismissed"):
        finding = Finding("", "behavior", "bug", "high", True, status)
        with pytest.raises(ValueError, match="disposition"):
            reviewed(state, findings=(finding,))


def test_fresh_recurrence_reopens_verified_id_with_current_failure_history():
    state, _ = reviewed(ready_for_review(candidate()))
    # A verified record from an earlier current verdict is preserved during feedback.
    from cpipe.delivery.findings import FindingRecord, disposition
    old = FindingRecord(Finding("REV-001", "behavior", "same bug", "high", True, "open"))
    old = disposition(old, "verified", artifact=state.artifact, scope=state.plan.envelope.scope,
                      verdict="old", reviewed_range="base..tree2", evidence_ref="evidence/old.json",
                      receipt=receipt(state), policy_authorized=True)
    state = replace(state, findings=(old,))
    state, _ = step(state, "feedback_received", "review", action="ready")
    state = claim(state, "sentry", "new-review-session")
    repeated = Finding("", "behavior", "same bug", "high", True, "open")
    failed = Verification("REV-001", receipt(state, outcome=Outcome.FAIL), "evidence/fail.json")
    reopened, _ = reviewed(state, findings=(repeated,), verifications=(failed,),
                           receipts=(receipt(state, "review", Outcome.FAIL),))
    assert reopened.findings[0].finding.id == "REV-001"
    assert reopened.findings[0].finding.disposition == "open"
    assert len(reopened.findings[0].history) == 2


def test_changed_contract_must_not_resume_old_builder_session():
    state = candidate()
    changed = replace(state.plan, goal="A materially different acceptance goal")
    proposal, intents = step(state, "feedback_received", "approve_envelope", plan=changed)
    assert intents[0][1]["resume"] == ""
    approved, _ = step(proposal, "envelope_approved", "tests")
    approved = claim(approved)
    building, intents = step(approved, "activity_observed", "implement",
                             receipts=(receipt(approved, outcome=Outcome.FAIL),))
    assert intents[0][1]["resume"] == ""


def test_draft_grant_is_exact_and_readback_is_required_before_completion():
    p = plan()
    granted = replace(p.envelope, capabilities=frozenset({"local", "push", "draft_pr"}))
    state = candidate(envelope=granted, completion_target="draft")
    state = ready_for_review(state)
    state, intents = reviewed(state)
    assert state.phase == "publishing" and intents[0][1]["action"] == "publish"
    state = claim(state)
    target = ("owner/repo", "feature", "main", state.artifact, "draft")
    with pytest.raises(ValueError, match="readback"):
        step(state, "publication_observed", target=("other",) + target[1:])
    done, intents = step(state, "publication_observed", target=target)
    assert done.phase == "completed" and not intents


@pytest.mark.parametrize("field,value", [("approved", 1), ("version", True),
                                        ("phase", "made_up"), ("receipts", ["fake"]),
                                        ("findings", ["fake"])])
def test_snapshot_rejects_unsafe_serialized_values(field, value):
    state = candidate()
    payload = json.loads(json.dumps(state.to_dict()))
    payload[field] = value
    with pytest.raises(ValueError):
        State.from_dict(payload)


def test_event_roundtrip_keeps_complete_provenance_and_rejects_unknown_authority():
    state = candidate()
    fact = event(state, "feedback_received", action="change")
    serialized = json.loads(json.dumps(fact.to_dict()))
    assert Event.from_dict(serialized) == fact
    serialized["approve_merge"] = True
    with pytest.raises(ValueError):
        Event.from_dict(serialized)
