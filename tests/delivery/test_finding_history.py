from dataclasses import replace
import pytest
from cpipe.delivery.contracts import Outcome, Receipt
from cpipe.delivery.findings import Finding, FindingRecord, disposition, merge_findings


def record():
    return FindingRecord(Finding("REV-001", "auth", "owner check", "high", True, "open"), ())


def proof(artifact="tree2", outcome=Outcome.PASS, observed=True):
    return Receipt("auth", artifact, "scope1", "inputs", "env", "check", outcome, observed)


def change(value, state, **kwargs):
    fields = dict(artifact="tree2", scope="scope1", verdict="verdict2", reviewed_range="tree1..tree2",
                  evidence_ref="evidence/result.json", receipt=proof(), policy_authorized=True)
    fields.update(kwargs)
    return disposition(value, state, **fields)


def test_explicit_current_verification_and_fresh_recurrence_preserve_history():
    initial = record()
    verified = change(initial, "verified")
    assert initial.finding.disposition == "open"
    assert verified.finding.id == initial.finding.id
    assert verified.finding.disposition == "verified"
    assert len(verified.history) == 1
    reopened = change(verified, "open", artifact="tree3", verdict="verdict3",
                      reviewed_range="tree2..tree3", receipt=proof("tree3", Outcome.FAIL))
    assert reopened.finding.disposition == "open"
    assert len(reopened.history) == 2
    assert reopened.history[0] == verified.history[0]
    assert FindingRecord.from_dict(reopened.to_dict()) == reopened


@pytest.mark.parametrize("kwargs", [
    {"receipt": None}, {"receipt": proof("tree1")},
    {"receipt": proof(observed=False)}, {"receipt": proof(outcome=Outcome.UNAVAILABLE)},
    {"receipt": replace(proof(), obligation="other")},
    {"receipt": replace(proof(), scope="other")}, {"verdict": ""},
    {"reviewed_range": ""}, {"evidence_ref": "../private"},
    {"policy_authorized": False}, {"policy_authorized": "true"},
])
def test_disposition_cannot_waive_missing_or_stale_proof_or_authority(kwargs):
    for state in ("dismissed", "verified"):
        with pytest.raises(ValueError):
            change(record(), state, **kwargs)


def test_serious_in_scope_dismissal_requires_explicit_scope_authority():
    with pytest.raises(ValueError, match="scope"):
        change(record(), "dismissed")
    dismissed = change(record(), "dismissed", scope_authorized=True)
    assert dismissed.finding.disposition == "dismissed"
    assert dismissed.history[-1].criterion == "auth"


def test_no_op_and_unsupported_dispositions_are_rejected():
    with pytest.raises(ValueError):
        change(record(), "open")
    with pytest.raises(ValueError):
        change(record(), "waived")


def test_stable_ids_do_not_collide_with_nonsequential_existing_ids():
    old = Finding("REV-002", "a", "one", "high", True, "open")
    incoming = Finding("", "b", "two", "high", True, "open")
    findings = merge_findings([old], [incoming])
    assert findings[0].id == "REV-002"
    assert findings[1].id == "REV-003"
