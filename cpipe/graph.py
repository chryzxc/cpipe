"""The delivery graph. Nodes are run by ``round.py``; where to go next is decided here, in code, never by a model.

    setup → build → checks ─pass→ handoff        (card → review: the PR is ready to test)
                ↑      │fail (< limit)
                └──────┘                           at the limit → stuck (card → blocked)
    build ─needs_user→ ask                         (card → blocked with the engine's question)

An engine outage (rate_limited / unavailable / failed) ends the round with that status.
"""

from __future__ import annotations

# Where a round stops, and the runtime status it reports.
TERMINAL = {"handoff": "needs_review", "ask": "blocked", "stuck": "blocked",
            "rate_limited": "rate_limited", "unavailable": "unavailable", "failed": "failed"}
ENGINE_DOWN = ("rate_limited", "unavailable", "failed")


def next_node(node: str, outcome: str, rounds: dict, limits: dict) -> str:
    """``outcome``: ok | fail | needs_user | rate_limited | unavailable | failed. ``rounds`` counts the failures
    so far in this round, per loop (the failure that led here included)."""
    if outcome in ENGINE_DOWN:
        return outcome
    if node == "setup":
        return "build"
    if node == "build":
        return "ask" if outcome == "needs_user" else "checks"
    if node == "checks":
        if outcome == "ok":
            return "handoff"
        return "build" if rounds.get("checks", 0) < limits["checks"] else "stuck"
    raise ValueError(f"unknown node {node!r}")
