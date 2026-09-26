from __future__ import annotations

SCHEMA_VERSION = 3


def schema_sql() -> str:
    return r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archive_files (
    path TEXT PRIMARY KEY,
    page_number INTEGER NOT NULL UNIQUE,
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
    forwarded_datetime_raw TEXT,
    message_type TEXT NOT NULL,
    is_service INTEGER NOT NULL DEFAULT 0 CHECK (is_service IN (0, 1)),
    is_joined INTEGER NOT NULL DEFAULT 0 CHECK (is_joined IN (0, 1)),
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
CREATE INDEX IF NOT EXISTS idx_messages_service ON messages(is_service);

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

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_vocab USING fts5vocab(messages_fts, 'row');

-- Discussions (schema v3): reconstructed conversation units, rebuilt after
-- every index change by storage/discussions.py. discussion_key is stable
-- across reindexes (derived from Telegram message ids).
CREATE TABLE IF NOT EXISTS discussions (
    id INTEGER PRIMARY KEY,
    discussion_key TEXT NOT NULL UNIQUE,
    root_row_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    start_utc TEXT,
    end_utc TEXT,
    message_count INTEGER NOT NULL,
    author_count INTEGER NOT NULL,
    text_normalized TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    builder_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discussion_messages (
    discussion_id INTEGER NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    message_row_id INTEGER NOT NULL PRIMARY KEY,
    position INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discussion_messages_discussion ON discussion_messages(discussion_id, position);

CREATE VIRTUAL TABLE IF NOT EXISTS discussions_fts USING fts5(
    text_normalized,
    content='discussions',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text_normalized, author_normalized, forwarded_from)
    VALUES (new.id, new.text_normalized, new.author_normalized, new.forwarded_from);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text_normalized, author_normalized, forwarded_from)
    VALUES ('delete', old.id, old.text_normalized, old.author_normalized, old.forwarded_from);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text_normalized, author_normalized, forwarded_from)
    VALUES ('delete', old.id, old.text_normalized, old.author_normalized, old.forwarded_from);
    INSERT INTO messages_fts(rowid, text_normalized, author_normalized, forwarded_from)
    VALUES (new.id, new.text_normalized, new.author_normalized, new.forwarded_from);
END;
"""
