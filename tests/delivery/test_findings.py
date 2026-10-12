from cpipe.delivery.findings import Finding, merge_findings, blocking, stalled


def test_duplicate_finding_keeps_its_id_and_open_serious_issue_blocks():
    old = Finding("REV-001", "auth", "missing owner check", "high", True, "open")
    new = Finding("", "auth", "missing owner check", "high", True, "open")
    assert merge_findings([old], [new]) == [old]
    assert blocking([old])
    assert stalled(3, {"REV-001"}, {"REV-001"}, 1)


def test_out_of_scope_suggestion_does_not_expand_implementation():
    f = Finding("REV-002", "style", "rename helper", "low", False, "open")
    assert not blocking([f])


def test_resolved_finding_stays_resolved_until_explicitly_reopened():
    old = Finding("REV-001", "auth", "missing owner check", "high", True, "verified")
    repeat = Finding("", "auth", "missing owner check", "high", True, "open")
    assert merge_findings([old], [repeat]) == [old]


def test_low_in_scope_advice_never_blocks():
    assert not blocking([Finding("REV-003", "review", "name could be clearer", "low", True, "open")])
    assert blocking([Finding("REV-004", "review", "off by one on empty input", "medium", True, "open")])


def test_a_loop_that_trades_one_finding_for_another_is_stalled():
    assert not stalled(2, {"REV-001"}, {"REV-002"}, 1)  # one round without progress is allowed
    assert stalled(3, {"REV-002"}, {"REV-003"}, 2)  # two in a row is not converging
    assert stalled(5, {"REV-001", "REV-002"}, {"REV-003"}, 0)  # out of rounds
