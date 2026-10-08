from cpipe.delivery.findings import Finding, merge_findings, blocking, needs_diagnosis


def test_duplicate_finding_keeps_its_id_and_open_serious_issue_blocks():
    old = Finding("REV-001", "auth", "missing owner check", "high", True, "open")
    new = Finding("", "auth", "missing owner check", "high", True, "open")
    assert merge_findings([old], [new]) == [old]
    assert blocking([old])
    assert needs_diagnosis(3, {"REV-001"}, {"REV-001"})


def test_out_of_scope_suggestion_does_not_expand_implementation():
    f = Finding("REV-002", "style", "rename helper", "low", False, "open")
    assert not blocking([f])


def test_resolved_finding_stays_resolved_until_explicitly_reopened():
    old = Finding("REV-001", "auth", "missing owner check", "high", True, "verified")
    repeat = Finding("", "auth", "missing owner check", "high", True, "open")
    assert merge_findings([old], [repeat]) == [old]
