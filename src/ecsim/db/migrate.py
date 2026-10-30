"""Ordered, idempotent SQL migrations named NNNN_description.sql."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from ecsim.app.config import MIGRATIONS_DIR

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def discover(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    found = []
    for p in sorted(directory.glob("*.sql")):
        m = _NAME.match(p.name)
        if not m:
            raise ValueError(f"bad migration filename: {p.name}")
        found.append((int(m.group(1)), p))
    versions = [v for v, _ in found]
    if len(versions) != len(set(versions)):
        raise ValueError("duplicate migration version")
    return found


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations in one transaction each. Returns applied versions."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for version, path in discover(directory):
        if version in done:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            conn.execute("BEGIN")
            for stmt in _split(sql):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


def _split(sql: str) -> list[str]:
    """Split on semicolons at line ends (migrations contain no triggers/strings with ';')."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return row[0] or 0
