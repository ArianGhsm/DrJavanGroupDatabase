from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Iterable

from drjavanbot.domain import MessageRecord
from drjavanbot.ingest import ArchiveFile, TelegramHTMLParser, discover_archive_files
from drjavanbot.ingest.parser import content_hash_for
from drjavanbot.normalization import normalize_author
from .migrations import ensure_schema
from .schema import SCHEMA_VERSION


class IndexIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IndexReport:
    mode: str
    archive_files: int
    indexed_files: int
    skipped_files: int
    removed_files: int
    messages: int
    service_messages: int
    db_size_bytes: int
    elapsed_seconds: float


def connect_database(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    if not readonly:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def full_reindex(
    archive_dir: Path,
    db_path: Path,
    *,
    parser: TelegramHTMLParser | None = None,
) -> IndexReport:
    started = time.perf_counter()
    parser = parser or TelegramHTMLParser()
    sources = discover_archive_files(archive_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{db_path.name}.", suffix=".tmp", dir=db_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    temp_path.unlink(missing_ok=True)
    connection: sqlite3.Connection | None = None
    try:
        connection = connect_database(temp_path)
        ensure_schema(connection)
        previous_author: str | None = None
        for source in sources:
            records = tuple(parser.parse_file(source))  # parse fully before publication
            records, previous_author = _resolve_joined_authors(records, previous_author)
            with connection:
                _publish_source(connection, source, records, parser.parser_version)
        _validate_database(connection, expected_files=len(sources))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.close()
        connection = None
        _prepare_target_for_swap(db_path)
        os.replace(temp_path, db_path)
        _remove_sidecars(temp_path)
        return _report(db_path, "full", len(sources), len(sources), 0, 0, started)
    except Exception:
        if connection is not None:
            connection.close()
        temp_path.unlink(missing_ok=True)
        _remove_sidecars(temp_path)
        raise


def incremental_index(
    archive_dir: Path,
    db_path: Path,
    *,
    parser: TelegramHTMLParser | None = None,
) -> IndexReport:
    if not db_path.exists():
        return full_reindex(archive_dir, db_path, parser=parser)
    started = time.perf_counter()
    parser = parser or TelegramHTMLParser()
    sources = discover_archive_files(archive_dir)
    source_by_name = {_source_key(item): item for item in sources}
    connection = connect_database(db_path)
    try:
        ensure_schema(connection)
        manifest = {
            row["path"]: row["sha256"]
            for row in connection.execute("SELECT path, sha256 FROM archive_files")
        }
        removed = sorted(set(manifest) - set(source_by_name))

        changed_pages = {
            source.page_number
            for source in sources
            if manifest.get(_source_key(source)) != source.sha256
        }
        # A changed page can alter inherited author identity on joined messages
        # at the start of subsequent unchanged pages. Propagate only while the
        # next page actually depends on its predecessor.
        to_publish = set(changed_pages)
        page_map = {source.page_number: source for source in sources}
        parsed_cache: dict[int, tuple[MessageRecord, ...]] = {}
        queue = sorted(changed_pages)
        while queue:
            page = queue.pop(0)
            next_page = page + 1
            next_source = page_map.get(next_page)
            if next_source is None or next_page in to_publish:
                continue
            next_records = parsed_cache.setdefault(next_page, tuple(parser.parse_file(next_source)))
            first_content = next((r for r in next_records if not r.is_service), None)
            if first_content is not None and first_content.is_joined and first_content.author is None:
                to_publish.add(next_page)
                queue.append(next_page)

        # Parse every page that will be published before any mutation. A
        # malformed changed file therefore leaves the last-known-good index intact.
        for page in sorted(to_publish):
            source = page_map[page]
            parsed_cache.setdefault(page, tuple(parser.parse_file(source)))

        if removed:
            with connection:
                connection.executemany("DELETE FROM archive_files WHERE path=?", ((name,) for name in removed))

        published = 0
        for page in sorted(to_publish):
            source = page_map[page]
            records = parsed_cache[page]
            previous_author = _previous_author(connection, page)
            records, _ = _resolve_joined_authors(records, previous_author)
            with connection:
                _publish_source(connection, source, records, parser.parser_version)
            published += 1

        _validate_database(connection, expected_files=len(sources))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return _report(
            db_path,
            "incremental",
            len(sources),
            published,
            len(sources) - published,
            len(removed),
            started,
            connection=connection,
        )
    finally:
        connection.close()


def database_health(db_path: Path) -> dict[str, int | str | bool | None]:
    if not db_path.exists():
        return {"healthy": False, "detail": "index database does not exist", "schema_version": None}
    connection = connect_database(db_path, readonly=True)
    try:
        version = ensure_schema_readonly(connection)
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        fk = connection.execute("PRAGMA foreign_key_check").fetchall()
        files = connection.execute("SELECT count(*) FROM archive_files").fetchone()[0]
        messages = connection.execute("SELECT count(*) FROM messages").fetchone()[0]
        fts = connection.execute("SELECT count(*) FROM messages_fts").fetchone()[0]
        healthy = quick == "ok" and not fk and messages == fts and version == SCHEMA_VERSION
        return {
            "healthy": healthy,
            "detail": "ok" if healthy else f"quick={quick}; fk={len(fk)}; messages={messages}; fts={fts}",
            "schema_version": version,
            "archive_files": files,
            "messages": messages,
            "fts_rows": fts,
        }
    finally:
        connection.close()


def ensure_schema_readonly(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    if row is None:
        raise IndexIntegrityError("schema version missing")
    return int(row[0])


def _publish_source(
    connection: sqlite3.Connection,
    source: ArchiveFile,
    records: Iterable[MessageRecord],
    parser_version: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    connection.execute("DELETE FROM archive_files WHERE path=?", (_source_key(source),))
    connection.execute(
        "INSERT INTO archive_files(path, page_number, sha256, size_bytes, parser_version, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_source_key(source), source.page_number, source.sha256, source.size_bytes, parser_version, now),
    )
    for record in records:
        cursor = connection.execute(
            """
            INSERT INTO messages(
                message_id, dom_id, source_file, source_page, source_order,
                datetime_utc, datetime_raw, author, author_normalized,
                text_raw, text_normalized, reply_to_message_id, reply_source_file,
                forwarded_from, forwarded_datetime_raw, message_type, is_service, is_joined, source_locator,
                source_sha256, content_hash, ingest_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.message_id, record.dom_id, record.source_file, record.source_page, record.source_order,
                _datetime_utc(record), record.datetime_raw, record.author, record.author_normalized,
                record.text_raw, record.text_normalized, record.reply_to_message_id, record.reply_source_file,
                record.forwarded_from, record.forwarded_datetime_raw, record.message_type.value, int(record.is_service), int(record.is_joined),
                record.source_locator, record.source_sha256, record.content_hash, record.ingest_version,
            ),
        )
        row_id = int(cursor.lastrowid)
        connection.executemany(
            "INSERT INTO message_links(message_row_id, ordinal, href, label) VALUES (?, ?, ?, ?)",
            ((row_id, i, link.href, link.label) for i, link in enumerate(record.links)),
        )
        connection.executemany(
            "INSERT INTO message_media(message_row_id, ordinal, kind, path, label) VALUES (?, ?, ?, ?, ?)",
            ((row_id, i, media.kind, media.path, media.label) for i, media in enumerate(record.media)),
        )


def _datetime_utc(record: MessageRecord) -> str | None:
    if record.datetime is None:
        return None
    value = record.datetime
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _resolve_joined_authors(
    records: tuple[MessageRecord, ...],
    previous_author: str | None,
) -> tuple[tuple[MessageRecord, ...], str | None]:
    current = previous_author
    out: list[MessageRecord] = []
    for record in records:
        if record.is_service:
            out.append(record)
            continue
        resolved = record
        if record.is_joined and not record.author and current:
            resolved = replace(record, author=current, author_normalized=normalize_author(current))
            resolved = replace(resolved, content_hash=content_hash_for(resolved))
        if resolved.author:
            current = resolved.author
        out.append(resolved)
    return tuple(out), current


def _previous_author(connection: sqlite3.Connection, page: int) -> str | None:
    row = connection.execute(
        "SELECT author FROM messages WHERE is_service=0 AND source_page < ? AND author IS NOT NULL "
        "ORDER BY source_page DESC, source_order DESC LIMIT 1",
        (page,),
    ).fetchone()
    return str(row[0]) if row else None


def _validate_database(connection: sqlite3.Connection, *, expected_files: int) -> None:
    quick = connection.execute("PRAGMA quick_check").fetchone()[0]
    if quick != "ok":
        raise IndexIntegrityError(f"SQLite quick_check failed: {quick}")
    fk = connection.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        raise IndexIntegrityError(f"foreign key check failed with {len(fk)} rows")
    file_count = connection.execute("SELECT count(*) FROM archive_files").fetchone()[0]
    if file_count != expected_files:
        raise IndexIntegrityError(f"expected {expected_files} archive files, indexed {file_count}")
    message_count = connection.execute("SELECT count(*) FROM messages").fetchone()[0]
    fts_count = connection.execute("SELECT count(*) FROM messages_fts").fetchone()[0]
    if message_count != fts_count:
        raise IndexIntegrityError(f"FTS row count mismatch: messages={message_count}, fts={fts_count}")
    connection.execute("INSERT INTO messages_fts(messages_fts) VALUES('integrity-check')")


def _report(
    db_path: Path,
    mode: str,
    archive_files: int,
    indexed_files: int,
    skipped_files: int,
    removed_files: int,
    started: float,
    *,
    connection: sqlite3.Connection | None = None,
) -> IndexReport:
    own = connection is None
    conn = connection or connect_database(db_path, readonly=True)
    try:
        messages = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        services = conn.execute("SELECT count(*) FROM messages WHERE is_service=1").fetchone()[0]
    finally:
        if own:
            conn.close()
    return IndexReport(
        mode=mode,
        archive_files=archive_files,
        indexed_files=indexed_files,
        skipped_files=skipped_files,
        removed_files=removed_files,
        messages=messages,
        service_messages=services,
        db_size_bytes=db_path.stat().st_size if db_path.exists() else 0,
        elapsed_seconds=time.perf_counter() - started,
    )


def _prepare_target_for_swap(db_path: Path) -> None:
    if not db_path.exists():
        _remove_sidecars(db_path)
        return
    connection = connect_database(db_path)
    try:
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result is not None and int(result[0]) != 0:
            raise IndexIntegrityError("cannot atomically replace an index with active SQLite readers/writers")
    finally:
        connection.close()
    _remove_sidecars(db_path)


def _source_key(source: ArchiveFile) -> str:
    return source.logical_path or source.path.name


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)
