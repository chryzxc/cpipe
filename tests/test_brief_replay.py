"""A replay re-renders a past card's REQUEST through today's briefs."""
from cpipe import brief_replay, submit


def test_replay_brief_is_todays_brief_for_the_old_request(monkeypatch):
    monkeypatch.setattr(submit, "_role", lambda role: "bot")
    monkeypatch.setattr(submit, "_conventions", lambda project, base="": "CONVENTIONS: x")
    old = submit.chained_card("plan", "Add reminders", "Send SMS reminders.", "p1", "t_parent")[0]
    stale = old[old.index("--body") + 1].replace("LARGE TASK", "OLD WORDING") + "\n" + submit.BUG_PLAN
    card = {"title": "Plan: Add reminders", "project_id": "p1", "body": stale}
    body = brief_replay.new_brief("plan", card)
    assert body.startswith(old[old.index("--body") + 1]) and "OLD WORDING" not in body
    assert submit.BUG_PLAN in body and body.endswith("CONVENTIONS: x")
