from cpipe.delivery.context import packet, prompt


def test_delta_is_appended_without_rewriting_stable_context():
    p = packet("fix button", "scope1", ["visual", "review"], "tree1",
               [], ["receipt1"], "implement")
    stable = prompt(p, "")
    changed = prompt(p, "Change only the label to Recorded")
    assert changed.startswith(stable)
    assert "Recorded" not in stable
    assert changed.endswith("Change only the label to Recorded")
    assert p["schema"] == 1
