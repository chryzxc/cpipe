"""Small native Kanban bridge; normal Hermes dispatch is unchanged."""
import os
import socket
import uuid
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


def connect(board=None):
    return kbc.connect(board=board)


def new_claimer():
    return f'{socket.gethostname() or "unknown"}:{os.getpid()}:cpipe-{uuid.uuid4().hex[:12]}'


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
