import os
import sqlite3
import stat
import pytest
from cpipe.delivery.journal import Journal


def test_known_prototype_schema_migrates_without_losing_facts(tmp_path):
    from cpipe.delivery.journal import SCHEMA
    path = tmp_path / "facts.db"
    with sqlite3.connect(path) as db:
        # The prototype used different indentation and no user_version.
        db.executescript(SCHEMA.replace("  id", "          id").replace("  delivery", "          delivery"))
        db.execute("INSERT INTO delivery VALUES ('d1',1,'{\"phase\":\"checking\"}')")
    path.chmod(0o600)
    with Journal(path) as j:
        assert j.read("d1") == (1, {"phase": "checking"})
        assert j.db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_symlink_database_is_rejected_without_touching_target(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"private data")
    target.chmod(0o644)
    link = tmp_path / "facts.db"
    link.symlink_to(target)
    before = (target.read_bytes(), target.stat().st_mode)
    with pytest.raises(ValueError, match="unsafe"):
        Journal(link)
    assert (target.read_bytes(), target.stat().st_mode) == before


def test_symlink_parent_cannot_redirect_store(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe"):
        Journal(link / "facts.db")
    assert not (target / "facts.db").exists()


def test_hardlink_database_is_rejected_without_changing_permissions(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"private data")
    target.chmod(0o600)
    link = tmp_path / "facts.db"
    os.link(target, link)
    with pytest.raises(ValueError, match="unsafe"):
        Journal(link)
    assert target.read_bytes() == b"private data"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("version,foreign", [(2, False), (0, True)])
def test_foreign_or_newer_database_is_not_modified(tmp_path, version, foreign):
    path = tmp_path / "facts.db"
    if foreign:
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE unrelated (value TEXT)")
    else:
        with Journal(path):
            pass
        with sqlite3.connect(path) as db:
            db.execute(f"PRAGMA user_version={version}")
    path.chmod(0o600)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="schema"):
        Journal(path)
    assert path.read_bytes() == before


def test_unsafe_existing_permissions_are_not_silently_repaired(tmp_path):
    path = tmp_path / "facts.db"
    with Journal(path):
        pass
    path.chmod(0o644)
    with pytest.raises(ValueError, match="unsafe"):
        Journal(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_private_store_and_profile_paths_are_distinct(tmp_path):
    default = tmp_path / "home"
    named = default / "profiles" / "custom"
    named.mkdir(parents=True)
    with Journal.for_profile(default) as a, Journal.for_profile(named) as b:
        a.append("d1", 0, "one", {"origin": "default"})
        b.append("d1", 0, "one", {"origin": "custom"})
        assert a.read("d1")[1] != b.read("d1")[1]
    for home in (default, named):
        assert stat.S_IMODE((home / "cpipe-deliveries").stat().st_mode) == 0o700
        path = home / "cpipe-deliveries" / "facts.db"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 1


@pytest.mark.parametrize("field,value", [("delivery_id", ""), ("expected_version", True),
                                        ("expected_version", -1), ("key", "")])
def test_malformed_write_is_rejected_before_projection_change(tmp_path, field, value):
    values = dict(delivery_id="d1", expected_version=0, key="one", snapshot={"phase": "ready"})
    values[field] = value
    with Journal(tmp_path / "facts.db") as j:
        with pytest.raises(ValueError):
            j.append(**values)
        assert j.read("d1") == (0, None)


def test_duplicate_intent_id_in_single_event_is_rejected(tmp_path):
    with Journal(tmp_path / "facts.db") as j:
        with pytest.raises(ValueError, match="duplicate"):
            j.append("d1", 0, "one", {}, (("i1", {}), ("i1", {})))
        assert j.read("d1") == (0, None)
        assert j.pending() == []
