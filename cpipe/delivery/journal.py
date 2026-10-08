"""Transactional engineering facts and an outbox, never native worker claims."""
import json
import os
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote
from .contracts import identity

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE delivery (
  id TEXT PRIMARY KEY, version INTEGER NOT NULL, snapshot TEXT NOT NULL);
CREATE TABLE event (
  delivery TEXT NOT NULL, version INTEGER NOT NULL,
  key TEXT NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(delivery, version), UNIQUE(delivery, key));
CREATE TABLE intent (
  id TEXT PRIMARY KEY, payload TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0);
"""


def schema_signature(db):
    return [(kind, name, table, " ".join(sql.split()) if sql else None)
            for kind, name, table, sql in db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]


def expected_schema():
    with sqlite3.connect(":memory:") as db:
        db.executescript(SCHEMA)
        return schema_signature(db)


def safe_file(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError("unsafe journal file")
    return info


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Journal:
    def __init__(self, path: Path):
        path = Path(path).absolute()
        if path.resolve() != path:
            raise ValueError("unsafe symlink journal path")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        created = False
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
            created = True
        original = safe_file(path)
        self.db = sqlite3.connect(f"file:{quote(str(path))}?mode=rw", uri=True,
                                  timeout=10, isolation_level=None)
        try:
            current = safe_file(path)
            if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("unsafe replaced journal path")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if created:
                self.db.executescript("BEGIN IMMEDIATE;" + SCHEMA +
                                     f"PRAGMA user_version={SCHEMA_VERSION};COMMIT;")
            elif version not in {0, SCHEMA_VERSION} or schema_signature(self.db) != expected_schema():
                raise ValueError("unsupported journal schema")
            elif version == 0:
                # Only the exact previous cpipe schema is a supported migration.
                self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.db.execute("PRAGMA foreign_keys=ON")
        except BaseException:
            self.db.close()
            raise

    @classmethod
    def for_profile(cls, home):
        root = Path(home).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("profile home must be a directory")
        directory = root / "delivery-v2"
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError("unsafe profile store directory")
        return cls(directory / "facts.db")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def read(self, delivery_id):
        row = self.db.execute("SELECT version,snapshot FROM delivery WHERE id=?",
                              (delivery_id,)).fetchone()
        return (row[0], json.loads(row[1])) if row else (0, None)

    def append(self, delivery_id, expected_version, key, snapshot, intents=()):
        identity(delivery_id, "delivery_id")
        identity(key, "event key")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid expected version")
        if type(snapshot) is not dict:
            raise ValueError("snapshot must be a dictionary")
        intents = tuple(intents)
        ids = []
        for intent_id, intent in intents:
            identity(intent_id, "intent id")
            if type(intent) is not dict:
                raise ValueError("intent must be a dictionary")
            ids.append(intent_id)
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate intent id")
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
