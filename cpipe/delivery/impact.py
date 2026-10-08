from dataclasses import dataclass


@dataclass(frozen=True)
class Obligation:
    name: str
    inputs: frozenset[str] | None
    after: tuple[str, ...] = ()


def affected(obligations, changed, authority=True):
    obligations = tuple(obligations)
    by_name = {o.name: o for o in obligations}
    if len(by_name) != len(obligations):
        raise ValueError("duplicate obligation")
    visiting, visited = set(), set()

    def visit(name):
        if name not in by_name:
            raise ValueError("unknown obligation dependency")
        if name in visiting:
            raise ValueError("cyclic obligation dependencies")
        if name in visited:
            return
        visiting.add(name)
        for parent in by_name[name].after:
            visit(parent)
        visiting.remove(name)
        visited.add(name)

    for name in by_name:
        visit(name)
    changed = set(changed)
    covered = set().union(*(o.inputs or set() for o in obligations))
    if not authority or not changed <= covered:
        return set(by_name)
    invalid = {o.name for o in obligations if o.inputs is None or o.inputs & changed}
    while True:
        expanded = invalid | {o.name for o in obligations if set(o.after) & invalid}
        if expanded == invalid:
            return invalid
        invalid = expanded
