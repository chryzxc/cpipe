"""The delivery graph. Nodes are run by ``round.py``; where to go next is decided here, in code, never by a model.

    setup → plan → proposal                                 (card → review: the plan, for the user to approve)
    feedback on the plan: plan again; approved: tests, or split → done (independent parts: child deliveries)
    tests → build → checks → verify → pr → ci → handoff     (card → review: the PR is ready to test)
    tests first: guard tests pass and the new tests fail (red) before the change; build makes them pass (green)
                 ↑       │        │       │     │
                 └───────┴─fix────┴───────┴─────┘           a loop at its limit → stuck (card → blocked)
    build ─needs_user→ ask                                  (card → blocked with the engine's question)
    "ready" from the user: handoff → final → done           (checks on HEAD, the PR marked ready)

verify runs QA, the security checks and an independent review in parallel. An engine outage (rate_limited /
unavailable / failed) ends the round with that status; so does the round's wall clock (paused).
"""

from __future__ import annotations

# Where a round stops, and the runtime status it reports.
TERMINAL = {"proposal": "needs_review", "handoff": "needs_review", "reviewed": "needs_review", "verified": "needs_review", "ask": "blocked", "stuck": "blocked", "paused": "blocked",
            "done": "success", "rate_limited": "rate_limited", "unavailable": "unavailable", "failed": "failed"}
ENGINE_DOWN = ("rate_limited", "unavailable", "failed")
ENGINE_NODES = ("plan", "tests", "build", "review_stage", "verify")  # nodes the wall clock is checked before

EDGES = {
    "setup": {"ok": "plan", "approved": "tests", "content": "build", "review": "review_stage",
              "fix": "build", "verify": "checks", "ship": "checks", "blocked": "stuck"},
    "review_stage": {"ok": "reviewed", "blocked": "stuck"},
    "plan": {"ok": "proposal", "needs_user": "ask"},
    "split": {"ok": "done"},
    "tests": {"ok": "build", "fix": "tests", "limit": "stuck", "needs_user": "ask", "blocked": "stuck"},
    "build": {"ok": "checks", "needs_user": "ask"},
    "checks": {"ok": "verify", "fix": "build", "limit": "stuck", "blocked": "stuck"},
    "verify": {"ok": "pr", "fix": "build", "limit": "stuck", "blocked": "stuck"},
    "pr": {"ok": "ci", "fix": "build", "limit": "stuck", "blocked": "stuck"},
    "ci": {"ok": "handoff", "fix": "build", "limit": "stuck", "blocked": "stuck"},
    "final": {"ok": "done", "fix": "build", "limit": "stuck", "blocked": "stuck", "reverify": "checks"},
}


def next_node(node: str, outcome: str, ready: bool = False) -> str:
    """``ready``: the user accepted the delivery, so a passing round goes on to ``final`` instead of stopping."""
    if outcome in ENGINE_DOWN:
        return outcome
    try:
        to = EDGES[node][outcome]
    except KeyError:
        raise ValueError(f"no edge {node!r} --{outcome}-->") from None
    return "final" if to == "handoff" and ready else to


def loop_outcome(failed: list[str], rounds: dict, limits: dict, soft: tuple = ()) -> str:
    """Count one failure per failed loop: ``fix`` while every failed loop has budget left, ``limit`` once a hard
    loop is out of it. A soft loop out of budget passes (its findings go into the PR instead)."""
    for k in failed:
        rounds[k] = rounds.get(k, 0) + 1
    if any(rounds[k] > limits[k] for k in failed if k not in soft):
        return "limit"
    return "fix" if any(rounds[k] <= limits[k] for k in failed) else "ok"
