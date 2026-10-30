from __future__ import annotations

import sqlite3
from pathlib import Path


def open_db(path: Path | str) -> sqlite3.Connection:
    """Open the learner database with WAL, foreign keys and dict-like rows."""
    path = str(path)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def backup_to(conn: sqlite3.Connection, dest: Path | str) -> Path:
    """Consistent online backup to a single file."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    target = sqlite3.connect(str(dest))
    try:
        conn.backup(target)
    finally:
        target.close()
    return dest


def restore_from(src: Path | str, db_path: Path | str) -> None:
    """Replace db_path with the backup at src. Caller must reopen connections."""
    src_conn = sqlite3.connect(str(src))
    try:
        check = src_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise ValueError(f"backup failed integrity check: {check}")
        dst = sqlite3.connect(str(db_path))
        try:
            src_conn.backup(dst)
        finally:
            dst.close()
    finally:
        src_conn.close()
