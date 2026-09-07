from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Iterable

from drjavanbot.search.terms import distinctive_terms, informative_tokens
from drjavanbot.storage.database import connect_database, ensure_schema_readonly


def sqlite_corpus_hints(backend, seeds: Iterable[str], *, limit: int = 24) -> tuple[str, ...]:
    """Derive bounded vocabulary from messages that co-occur with plan concepts.

    This is intentionally not a second evidence store and adds no schema. Hints
    are extracted from the current canonical index and may only be used to form
    later lexical queries; they never become citations or facts by themselves.
    """
    db_path = getattr(backend, "db_path", None)
    if db_path is None:
        return ()
    path = Path(db_path)
    if not path.is_file():
        return ()
    seed_tokens: list[str] = []
    for seed in seeds:
        seed_tokens.extend(informative_tokens(seed))
    seed_tokens = list(dict.fromkeys(seed_tokens))[:12]
    if not seed_tokens:
        return ()
    expression = " OR ".join(_quote_fts(token) for token in seed_tokens)
    connection: sqlite3.Connection | None = None
    try:
        connection = connect_database(path, readonly=True)
        ensure_schema_readonly(connection)
        rows = connection.execute(
            "SELECT m.text_normalized FROM messages_fts JOIN messages m ON m.id=messages_fts.rowid "
            "WHERE messages_fts MATCH ? AND m.is_service=0 ORDER BY bm25(messages_fts) LIMIT 60",
            (expression,),
        ).fetchall()
        return distinctive_terms(
            (row["text_normalized"] for row in rows),
            exclude=seed_tokens,
            limit=max(1, min(limit, 48)),
        )
    except sqlite3.Error:
        return ()
    finally:
        if connection is not None:
            connection.close()


def _quote_fts(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


__all__ = ["sqlite_corpus_hints"]
