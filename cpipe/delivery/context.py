import json


def packet(goal, scope, obligations, artifact, findings, evidence, next_action):
    return {"schema": 1, "goal": goal, "scope": scope,
            "obligations": list(obligations), "artifact": artifact,
            "findings": list(findings), "evidence": list(evidence),
            "next_action": next_action}


def prompt(stable, delta):
    prefix = "Delivery contract:\n" + json.dumps(
        stable, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    return prefix + ("\nCurrent delta:\n" + delta if delta else "")
