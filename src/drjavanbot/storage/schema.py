from __future__ import annotations

SCHEMA_VERSION = 1


def schema_sql() -> str:
    """SQLite schema contract for stage 2.

    FTS is external-content so the canonical message row remains the source of
    truth. Stage 2 must keep FTS synchronized transactionally.
    """
    return r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archive_files (
    path TEXT PRIMARY KEY,
    page_number INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    parser_version TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    message_id INTEGER,
    dom_id TEXT NOT NULL,
    source_file TEXT NOT NULL REFERENCES archive_files(path) ON DELETE CASCADE,
    source_page INTEGER NOT NULL,
    source_order INTEGER NOT NULL,
    datetime_utc TEXT,
    datetime_raw TEXT,
    author TEXT,
    author_normalized TEXT,
    text_raw TEXT NOT NULL DEFAULT '',
    text_normalized TEXT NOT NULL DEFAULT '',
    reply_to_message_id INTEGER,
    reply_source_file TEXT,
    forwarded_from TEXT,
    message_type TEXT NOT NULL,
    is_service INTEGER NOT NULL DEFAULT 0 CHECK (is_service IN (0, 1)),
    source_locator TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    ingest_version TEXT NOT NULL,
    UNIQUE(source_file, dom_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_message_id ON messages(message_id);
CREATE INDEX IF NOT EXISTS idx_messages_source_order ON messages(source_page, source_order);
CREATE INDEX IF NOT EXISTS idx_messages_datetime ON messages(datetime_utc);
CREATE INDEX IF NOT EXISTS idx_messages_author ON messages(author_normalized);
CREATE INDEX IF NOT EXISTS idx_messages_reply ON messages(reply_to_message_id);
CREATE INDEX IF NOT EXISTS idx_messages_content_hash ON messages(content_hash);

CREATE TABLE IF NOT EXISTS message_links (
    message_row_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    href TEXT NOT NULL,
    label TEXT,
    PRIMARY KEY(message_row_id, ordinal)
);

CREATE TABLE IF NOT EXISTS message_media (
    message_row_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL,
    path TEXT,
    label TEXT,
    PRIMARY KEY(message_row_id, ordinal)
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text_normalized,
    author_normalized,
    forwarded_from,
    content='messages',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
"""
