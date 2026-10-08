def decide(category, attempt, repeats, repair_allowed=False, budget_remaining=True):
    if attempt < 0 or repeats < 0:
        raise ValueError("negative recovery counter")
    if not budget_remaining or category == "permanent":
        return "stop"
    if repeats >= 2:
        return "diagnose"
    if category == "transient":
        return "retry" if attempt < 2 else "diagnose"
    if category == "persistent":
        if not repair_allowed:
            return "wait"
        return "repair" if attempt == 0 else "diagnose"
    if category == "external_wait":
        return "wait"
    if category == "behavior":
        return "correct"
    return "diagnose"
