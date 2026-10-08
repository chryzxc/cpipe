"""Transactional engineering facts and an outbox, never native worker claims."""
import json
import os
import sqlite3
from pathlib import Path


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Journal:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS delivery (
          id TEXT PRIMARY KEY, version INTEGER NOT NULL, snapshot TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS event (
          delivery TEXT NOT NULL, version INTEGER NOT NULL,
          key TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY(delivery, version), UNIQUE(delivery, key));
        CREATE TABLE IF NOT EXISTS intent (
          id TEXT PRIMARY KEY, payload TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0);
        """)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def read(self, delivery_id):
        row = self.db.execute("SELECT version,snapshot FROM delivery WHERE id=?",
                              (delivery_id,)).fetchone()
        return (row[0], json.loads(row[1])) if row else (0, None)

    def append(self, delivery_id, expected_version, key, snapshot, intents=()):
        intents = tuple(intents)
        payload = encoded({"snapshot": snapshot, "intents": intents})
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute(
                "SELECT version,payload FROM event WHERE delivery=? AND key=?",
                (delivery_id, key)).fetchone()
            if previous:
                if previous[1] != payload:
                    raise ValueError("idempotency key reused with different payload")
                self.db.execute("COMMIT")
                return previous[0]
            version, _ = self.read(delivery_id)
            if version != expected_version:
                raise RuntimeError("delivery version changed")
            version += 1
            self.db.execute("INSERT INTO event VALUES (?,?,?,?)",
                            (delivery_id, version, key, payload))
            self.db.execute(
                "INSERT INTO delivery VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "version=excluded.version,snapshot=excluded.snapshot",
                (delivery_id, version, encoded(snapshot)))
            for intent_id, intent in intents:
                body = encoded(intent)
                old = self.db.execute("SELECT payload FROM intent WHERE id=?",
                                      (intent_id,)).fetchone()
                if old and old[0] != body:
                    raise ValueError("idempotency intent reused with different payload")
                if not old:
                    self.db.execute("INSERT INTO intent(id,payload) VALUES (?,?)",
                                    (intent_id, body))
            self.db.execute("COMMIT")
            return version
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def pending(self):
        return [(row[0], json.loads(row[1])) for row in self.db.execute(
            "SELECT id,payload FROM intent WHERE acknowledged=0 ORDER BY rowid")]

    def ack(self, intent_id):
        self.db.execute("UPDATE intent SET acknowledged=1 WHERE id=?", (intent_id,))
