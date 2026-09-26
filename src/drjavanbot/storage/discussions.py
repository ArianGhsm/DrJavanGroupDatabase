"""Reconstruct group discussions from individual Telegram messages.

The archive's unit of knowledge is a *discussion* — a question with its
answers, objections and follow-ups — not a single message. Telegram records
most of that structure explicitly: 63% of messages reply to another message
and consecutive messages by one author are "joined". A discussion is a tree:

* a message's parent is the message it replies to, or — for a joined
  continuation — the previous message by the same author;
* a message starts a new discussion when it has no parent, or when its parent
  is more than ``GAP_HOURS`` older (a reply to an old thread opens a new
  conversation that merely references it).

Very long discussions are split into consecutive chunks. Every chunk keeps the
opening message as its header so the chunk still states its topic.

Discussion keys are derived from Telegram message ids, which never change, so
derived knowledge (e.g. LLM study cards) survives a full reindex.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import sqlite3

DISCUSSION_BUILDER_VERSION = "discussions-1"
GAP_HOURS = 24.0
MAX_CHUNK_MESSAGES = 40
_MAX_TEXT_CHARS = 24_000


@dataclass(frozen=True, slots=True)
class DiscussionBuildReport:
    discussions: int
    messages: int
    singletons: int
    largest: int


def rebuild_discussions(connection: sqlite3.Connection) -> DiscussionBuildReport:
    """Recompute every discussion. Deterministic; runs inside one transaction."""
    rows = connection.execute(
        "SELECT id, message_id, reply_to_message_id, is_joined, datetime_utc, author_normalized, text_normalized "
        "FROM messages WHERE is_service=0 ORDER BY source_page, source_order"
    ).fetchall()
    index_by_message_id = {row[1]: i for i, row in enumerate(rows) if row[1] is not None}
    times = [_timestamp(row[4]) for row in rows]
    gap = GAP_HOURS * 3600.0

    roots: list[int] = [0] * len(rows)
    for i, row in enumerate(rows):
        parent: int | None = None
        target = index_by_message_id.get(row[2]) if row[2] is not None else None
        if target is not None and target < i:
            parent = target
        elif row[3] and i > 0:
            parent = i - 1
        if parent is None or (times[i] is not None and times[parent] is not None and times[i] - times[parent] > gap):
            roots[i] = i
        else:
            roots[i] = roots[parent]

    groups: dict[int, list[int]] = {}
    for i, root in enumerate(roots):
        groups.setdefault(root, []).append(i)

    connection.execute("DELETE FROM discussion_messages")
    connection.execute("DELETE FROM discussions")
    discussion_rows: list[tuple] = []
    member_rows: list[tuple] = []
    next_id = 1
    for root, members in sorted(groups.items()):
        root_row = rows[root]
        root_key = str(root_row[1]) if root_row[1] is not None else f"r{root_row[0]}"
        chunks = [members[start:start + MAX_CHUNK_MESSAGES] for start in range(0, len(members), MAX_CHUNK_MESSAGES)]
        for chunk_index, chunk in enumerate(chunks):
            ordered = chunk if chunk_index == 0 or chunk[0] == root else [root, *chunk]
            texts = [rows[i][6] or "" for i in ordered]
            text = "\n".join(value for value in texts if value)[:_MAX_TEXT_CHARS]
            authors = {rows[i][5] for i in chunk if rows[i][5]}
            stamps = [rows[i][4] for i in chunk if rows[i][4]]
            # The first chunk always keeps the bare key, so a discussion that
            # grows past MAX_CHUNK_MESSAGES never renames existing knowledge.
            key = root_key if chunk_index == 0 else f"{root_key}.{chunk_index + 1}"
            content_hash = hashlib.sha256("\x1f".join(str(rows[i][0]) + ":" + (rows[i][6] or "") for i in chunk).encode("utf-8")).hexdigest()[:24]
            discussion_rows.append((
                next_id, key, rows[root][0], chunk_index, len(chunks), min(stamps) if stamps else None,
                max(stamps) if stamps else None, len(chunk), len(authors), text, content_hash, DISCUSSION_BUILDER_VERSION,
            ))
            member_rows.extend((next_id, rows[i][0], position) for position, i in enumerate(chunk))
            next_id += 1

    connection.executemany(
        "INSERT INTO discussions(id, discussion_key, root_row_id, chunk_index, chunk_count, start_utc, end_utc, "
        "message_count, author_count, text_normalized, content_hash, builder_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        discussion_rows,
    )
    connection.executemany(
        "INSERT INTO discussion_messages(discussion_id, message_row_id, position) VALUES(?,?,?)",
        member_rows,
    )
    connection.execute("INSERT INTO discussions_fts(discussions_fts) VALUES('rebuild')")
    sizes = [row[7] for row in discussion_rows]
    return DiscussionBuildReport(
        discussions=len(discussion_rows),
        messages=len(member_rows),
        singletons=sum(1 for size in sizes if size == 1),
        largest=max(sizes, default=0),
    )


def _timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


__all__ = ["DISCUSSION_BUILDER_VERSION", "DiscussionBuildReport", "rebuild_discussions"]
