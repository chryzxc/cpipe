"""Small native Kanban bridge; normal Hermes dispatch is unchanged."""
from contextlib import contextmanager
from pathlib import Path
import os
import socket
import time
import uuid
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


def connect(board=None):
    return kbc.connect(board=board)


def new_claimer():
    return f'{socket.gethostname() or "unknown"}:{os.getpid()}:cpipe-{uuid.uuid4().hex[:12]}'


@contextmanager
def dispatch_paused(conn, wait=30):
    """Hold the board's dispatcher lock: Hermes skips its dispatch tick (it never spawns a worker) meanwhile.
    Without the lock helper, cpipe still archives and replaces any card something else took."""
    tick_lock = getattr(kbc, '_dispatch_tick_lock', None)
    if tick_lock is None:
        yield
        return
    path = Path(conn.execute('PRAGMA database_list').fetchone()[2])  # the board this connection writes
    end = time.monotonic() + wait
    while True:
        with tick_lock(path) as held:
            if held:
                yield
                return
        if time.monotonic() > end:
            raise ValueError('the board dispatcher stayed busy; the step retries')
        time.sleep(0.2)  # a dispatch tick is short


def get_task(conn, task_id):
    return kb.get_task(conn, task_id)


def claim_task(conn, task_id, *, claimer, ttl_seconds):
    return kb.claim_task(conn, task_id, claimer=claimer, ttl_seconds=ttl_seconds)


def heartbeat_claim(conn, task_id, *, claimer, ttl_seconds):
    return kb.heartbeat_claim(conn, task_id, claimer=claimer, ttl_seconds=ttl_seconds)


def complete(conn, task_id, *, run_id, summary, metadata):
    return kb.complete_task(conn, task_id, summary=summary, result=summary,
                            metadata=metadata, expected_run_id=run_id)


def block(conn, task_id, *, run_id, reason, kind):
    return bool(kb.block_task(conn, task_id, reason=reason, kind=kind, expected_run_id=run_id))


def take(conn, task_id, *, claimer, ttl_seconds):
    """Claim a ready card, or keep one this claimer already holds."""
    task = kb.get_task(conn, task_id)
    if task and task.status == 'running' and task.claim_lock == claimer:
        return task if kb.heartbeat_claim(conn, task_id, claimer=claimer, ttl_seconds=ttl_seconds) else None
    return kb.claim_task(conn, task_id, claimer=claimer, ttl_seconds=ttl_seconds)


def archive(conn, task_id):
    return kb.archive_task(conn, task_id)
