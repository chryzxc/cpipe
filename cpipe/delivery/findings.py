from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Finding:
    id: str
    criterion: str
    root_cause: str
    severity: str
    in_scope: bool
    disposition: str


def merge_findings(prior, incoming):
    result = list(prior)
    known = {(f.criterion, f.root_cause): f for f in result}
    ids = {f.id for f in result}
    next_number = 1
    for finding in incoming:
        signature = (finding.criterion, finding.root_cause)
        if signature in known:
            continue
        while f"REV-{next_number:03d}" in ids:
            next_number += 1
        finding = replace(finding, id=f"REV-{next_number:03d}")
        result.append(finding)
        known[signature] = finding
        ids.add(finding.id)
    return result


def blocking(findings):
    return any(f.in_scope and f.disposition not in {"verified", "dismissed"}
               for f in findings)


def needs_diagnosis(cycles, before, after):
    return cycles >= 3 or (cycles >= 2 and bool(after) and set(after) == set(before))
