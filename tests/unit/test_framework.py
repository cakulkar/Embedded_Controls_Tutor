import sqlite3

import pytest

from ecsim.app.config import Settings
from ecsim.app.events import EventBus
from ecsim.app.services import ServiceRegistry
from ecsim.app.toolchain import probe
from ecsim.db import migrate, open_db
from ecsim.db.migrate import current_version


def test_event_bus_isolates_failing_handler():
    bus, got = EventBus(), []
    bus.subscribe("t", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe("t", lambda **kw: got.append(kw["x"]))
    errors = bus.publish("t", x=1)
    assert got == [1] and len(errors) == 1


def test_event_bus_unsubscribe():
    bus, got = EventBus(), []
    unsub = bus.subscribe("t", lambda **kw: got.append(1))
    unsub()
    bus.publish("t")
    assert got == []


def test_registry_rejects_duplicates():
    r = ServiceRegistry()
    r.register("a", 1)
    with pytest.raises(KeyError):
        r.register("a", 2)
    assert r.get("a") == 1


def test_migrations_idempotent(tmp_path):
    conn = open_db(tmp_path / "x.db")
    assert migrate(conn) == [1, 2]
    assert migrate(conn) == []
    assert current_version(conn) == 2
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"attempt", "exercise", "learner", "hint_reveal", "solution_reveal", "ecu_run"} <= tables


def test_phase1_database_upgrades_in_place(tmp_path):
    """A Phase 1 learner database must upgrade to 0002 without losing evidence."""
    from ecsim.app.config import MIGRATIONS_DIR
    import shutil
    old = tmp_path / "m"
    old.mkdir()
    shutil.copy(MIGRATIONS_DIR / "0001_initial.sql", old)
    conn = open_db(tmp_path / "x.db")
    migrate(conn, old)
    conn.execute("INSERT INTO learner(name) VALUES ('Ana')")
    conn.execute("INSERT INTO attempt(learner_id, exercise_id, content_hash, submission, passed, score, feedback,"
                 " hints_used, solution_seen, independent) VALUES (1,'A01-E1','h','1',1,1,'ok',0,0,1)")
    assert migrate(conn) == [2]
    assert conn.execute("SELECT COUNT(*) FROM attempt").fetchone()[0] == 1


def test_foreign_keys_enforced(tmp_path):
    conn = open_db(tmp_path / "x.db")
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO hint_reveal(learner_id, exercise_id, level) VALUES (999,'X',1)")


def test_settings_roundtrip(tmp_path):
    s = Settings.load(tmp_path)
    s.c_timeout_s = 9.5
    s.save()
    assert Settings.load(tmp_path).c_timeout_s == 9.5
    assert (tmp_path / "workspace").is_dir()


def test_toolchain_probe_reports_all_tools():
    res = probe()
    assert {"gcc", "cmake", "scilab", "omc"} <= set(res)
    for t in res.values():
        assert t.available == (t.path is not None)
