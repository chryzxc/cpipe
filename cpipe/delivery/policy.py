"""Pure decisions; adapters must establish observation and consent provenance."""
from collections.abc import Iterable
from .contracts import Envelope, Receipt, Outcome

EXTERNAL = frozenset({"push", "draft_pr", "mark_ready"})


def permits(envelope: Envelope, action: str, repo: str, branch: str, base: str) -> bool:
    return (action in EXTERNAL and action in envelope.capabilities
            and (repo, branch, base) == (envelope.repo, envelope.branch, envelope.base))


def ready(envelope: Envelope, receipts: Iterable[Receipt], artifact: str) -> bool:
    latest = {r.obligation: r for r in receipts}
    return bool(envelope.required) and all(
        name in latest
        and latest[name].observed
        and latest[name].outcome is Outcome.PASS
        and latest[name].artifact == artifact
        and latest[name].scope == envelope.scope
        for name in envelope.required
    )
