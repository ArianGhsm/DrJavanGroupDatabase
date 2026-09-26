from __future__ import annotations

import sqlite3

from .schema import SCHEMA_VERSION, schema_sql


class SchemaError(RuntimeError):
    pass


def current_schema_version(connection: sqlite3.Connection) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
    ).fetchone()
    if not exists:
        return 0
    row = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 1


def ensure_schema(connection: sqlite3.Connection) -> int:
    connection.execute("PRAGMA foreign_keys=ON")
    version = current_schema_version(connection)
    if version == 0:
        connection.executescript(schema_sql())
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()
        return SCHEMA_VERSION
    if version > SCHEMA_VERSION:
        raise SchemaError(f"database schema {version} is newer than supported {SCHEMA_VERSION}")
    if version == 1:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
        if "is_joined" not in columns:
            connection.execute("ALTER TABLE messages ADD COLUMN is_joined INTEGER NOT NULL DEFAULT 0")
        if "forwarded_datetime_raw" not in columns:
            connection.execute("ALTER TABLE messages ADD COLUMN forwarded_datetime_raw TEXT")
        # Re-run idempotent DDL to create v2 indexes, vocab and sync triggers.
        connection.executescript(schema_sql())
        connection.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', '2')",
        )
        connection.commit()
        version = 2
    if version == 2:
        # v3 adds reconstructed discussions; tables are populated by the
        # indexer (rebuild_discussions) in the same run.
        from .discussions import rebuild_discussions

        connection.executescript(schema_sql())
        with connection:
            rebuild_discussions(connection)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        version = SCHEMA_VERSION
    if version != SCHEMA_VERSION:
        raise SchemaError(f"no migration path from schema {version} to {SCHEMA_VERSION}")
    return version
