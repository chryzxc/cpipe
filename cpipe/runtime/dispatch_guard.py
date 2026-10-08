"""Isolate the native dispatch compatibility boundary for runtime-owned cards.

Assignees are real profile/CLI names. Ownership is the cpipe provenance, not an
assignee prefix. Hermes currently has no public executor registry; this narrow
adapter prevents its stock profile spawner from racing the cpipe supervisor.
No core files are changed and non-cpipe cards retain the original dispatcher.
"""
from functools import wraps


def install():
    from hermes_cli import kanban_db_dispatch as dispatch
    original = getattr(dispatch, "_dispatch_lane_task", None)
    if not callable(original):
        raise RuntimeError("Hermes dispatch compatibility unavailable; no delivery may be queued")
    if getattr(original, "_cpipe_guard", False):
        return

    @wraps(original)
    def guarded(conn, row, assignee, result, **kwargs):
        from . import hermes_api as h
        if h.is_delivery(h.get_task(conn, row["id"])):
            result.skipped_nonspawnable.append(row["id"])
            return False
        return original(conn, row, assignee, result, **kwargs)

    guarded._cpipe_guard = True
    dispatch._dispatch_lane_task = guarded
