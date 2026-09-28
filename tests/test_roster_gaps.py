"""Every role team-config requires must map to an existing profile; the operator hears about
a gap once in the chat, and hears again when it is fixed."""
import runpy
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import roster_gaps  # noqa: E402


def home(tmp_path, roster_lines):
    team = tmp_path / roster_gaps.TEAM_CONFIG
    team.parent.mkdir(parents=True)
    team.write_text("bots:\n  planner: {mission: plans}\n  spike-explorer: {mission: spikes}\n"
                    "  reviewer: {mission: review}\nrouting:\n  x: planner\n")
    (tmp_path / "roster.yaml").write_text("roles:\n" + "".join(f"  {l}\n" for l in roster_lines))
    for name in ("archon", "probe"):
        (tmp_path / "profiles" / name).mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_gaps_cover_unmapped_blank_and_missing_profiles(tmp_path):
    h = home(tmp_path, ["planner: archon  # plans", "spike_explorer:", "reviewer: sentry"])
    assert roster_gaps.required_roles(h) == ["planner", "spike_explorer", "reviewer"]
    assert roster_gaps.roster_gaps(home=h) == {
        "spike_explorer": "no bot assigned", "reviewer": "assigned bot 'sentry' does not exist"}


def test_stall_alert_reports_a_role_gap_once_then_its_fix(tmp_path, monkeypatch):
    h = home(tmp_path, ["planner: archon", "spike_explorer: probe"])
    mod = runpy.run_path(str(SCRIPTS / "stall_alert.py"))
    mod["roster_stalls"].__globals__["HERMES_HOME"] = h
    message, state = mod["build_digest"](mod["roster_stalls"](0), {}, 0)
    assert "Role `reviewer` has no working bot" in message and "roster.yaml" in message
    assert mod["build_digest"](mod["roster_stalls"](0), state, 0)[0] == ""
    (h / "roster.yaml").write_text("roles:\n  planner: archon\n  spike_explorer: probe\n  reviewer: probe\n")
    assert "Moving again: role reviewer" in mod["build_digest"](mod["roster_stalls"](0), state, 0)[0]
