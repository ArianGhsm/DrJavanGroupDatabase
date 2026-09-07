import sqlite3
from pathlib import Path

from drjavanbot.storage.migrations import ensure_schema
from drjavanbot.storage.schema import SCHEMA_VERSION


def test_stage1_schema_migrates_to_v2(tmp_path: Path):
    db = tmp_path / "v1.sqlite3"
    con = sqlite3.connect(db)
    con.executescript('''
    CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO schema_meta(key,value) VALUES('schema_version','1');
    CREATE TABLE archive_files (
      path TEXT PRIMARY KEY, page_number INTEGER NOT NULL, sha256 TEXT NOT NULL,
      size_bytes INTEGER NOT NULL, parser_version TEXT NOT NULL, indexed_at TEXT NOT NULL
    );
    CREATE TABLE messages (
      id INTEGER PRIMARY KEY, message_id INTEGER, dom_id TEXT NOT NULL,
      source_file TEXT NOT NULL REFERENCES archive_files(path) ON DELETE CASCADE,
      source_page INTEGER NOT NULL, source_order INTEGER NOT NULL,
      datetime_utc TEXT, datetime_raw TEXT, author TEXT, author_normalized TEXT,
      text_raw TEXT NOT NULL DEFAULT '', text_normalized TEXT NOT NULL DEFAULT '',
      reply_to_message_id INTEGER, reply_source_file TEXT, forwarded_from TEXT,
      message_type TEXT NOT NULL, is_service INTEGER NOT NULL DEFAULT 0,
      source_locator TEXT NOT NULL, source_sha256 TEXT NOT NULL,
      content_hash TEXT NOT NULL, ingest_version TEXT NOT NULL,
      UNIQUE(source_file, dom_id)
    );
    CREATE TABLE message_links (message_row_id INTEGER NOT NULL, ordinal INTEGER NOT NULL, href TEXT NOT NULL, label TEXT, PRIMARY KEY(message_row_id, ordinal));
    CREATE TABLE message_media (message_row_id INTEGER NOT NULL, ordinal INTEGER NOT NULL, kind TEXT NOT NULL, path TEXT, label TEXT, PRIMARY KEY(message_row_id, ordinal));
    CREATE VIRTUAL TABLE messages_fts USING fts5(text_normalized, author_normalized, forwarded_from, content='messages', content_rowid='id');
    ''')
    con.commit()
    assert ensure_schema(con) == SCHEMA_VERSION
    columns = {row[1] for row in con.execute('PRAGMA table_info(messages)')}
    assert 'is_joined' in columns
    assert 'forwarded_datetime_raw' in columns
    assert con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
    con.close()
