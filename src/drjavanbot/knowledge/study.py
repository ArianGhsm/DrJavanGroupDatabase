"""Study the archive once: write a card for every substantive discussion.

Resumable and incremental — a discussion is (re)studied only when it has no
card yet or its content changed. Every source id in a card is checked
against the discussion's own messages before the card is stored.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import threading
import time
from typing import Callable

from drjavanbot.ai.provider import AuthenticationError, RateLimitError
from drjavanbot.ai.validation import ModelOutputError
from drjavanbot.brain.redaction import redact
from drjavanbot.brain.retriever import DiscussionRetriever
from drjavanbot.storage.database import connect_database
from .prompts import STUDY_PROMPT
from .store import KnowledgeStore, card_from_payload

_LOG = logging.getLogger(__name__)
MIN_MESSAGES = 3
MIN_AUTHORS = 2
_MESSAGE_CHARS = 900
_DISCUSSION_CHARS = 9000

Progress = Callable[[int, int], None]


@dataclass
class StudyReport:
    pending: int = 0
    studied: int = 0
    useful: int = 0
    failed: int = 0
    stopped_reason: str | None = None


def pending_discussions(db_path: Path, store: KnowledgeStore) -> list[tuple[int, str, str]]:
    """(discussion_id, key, content_hash) needing a card, largest first."""
    known = store.known_hashes()
    connection = connect_database(db_path, readonly=True)
    try:
        rows = connection.execute(
            "SELECT id, discussion_key, content_hash FROM discussions WHERE message_count>=? AND author_count>=? "
            "ORDER BY message_count DESC, id",
            (MIN_MESSAGES, MIN_AUTHORS),
        ).fetchall()
    finally:
        connection.close()
    return [(int(r[0]), r[1], r[2]) for r in rows if known.get(r[1]) != r[2]]


def study_archive(
    db_path: Path,
    store: KnowledgeStore,
    model,
    *,
    limit: int | None = None,
    workers: int = 4,
    progress: Progress | None = None,
    stop: threading.Event | None = None,
) -> StudyReport:
    todo = pending_discussions(db_path, store)
    if limit is not None:
        todo = todo[: max(0, limit)]
    report = StudyReport(pending=len(todo))
    retriever = DiscussionRetriever(db_path)
    stop = stop or threading.Event()
    lock = threading.Lock()

    def one(item: tuple[int, str, str]) -> None:
        if stop.is_set():
            return
        discussion_id, key, content_hash = item
        loaded = retriever.load([discussion_id])
        if not loaded:
            return
        discussion = loaded[0]
        messages, ids = _messages(discussion)
        for attempt in range(4):
            if stop.is_set():
                return
            try:
                payload = model.complete_json(stage="study", system=STUDY_PROMPT,
                                              user=json.dumps({"messages": messages}, ensure_ascii=False), max_tokens=900)
                break
            except RateLimitError:
                time.sleep(min(60, 5 * 2 ** attempt))
        else:
            raise RateLimitError("rate limited repeatedly")
        card = card_from_payload(key, content_hash, _verified(payload, ids))
        store.put(card, model="study")
        with lock:
            report.studied += 1
            report.useful += int(card.useful)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, item) for item in todo]
        for future in as_completed(futures):
            try:
                future.result()
            except AuthenticationError:
                report.stopped_reason = "authentication_failed"
                stop.set()
            except RateLimitError:
                report.stopped_reason = "rate_limited"
                stop.set()
            except (ModelOutputError, ValueError) as exc:
                with lock:
                    report.failed += 1
                _LOG.warning("study_card_failed error_class=%s", type(exc).__name__)
            if progress is not None:
                progress(report.studied + report.failed, report.pending)
    if stop.is_set() and report.stopped_reason is None:
        report.stopped_reason = "stopped"
    return report


def _messages(discussion) -> tuple[list[dict], set[int]]:
    out, ids, budget = [], set(), _DISCUSSION_CHARS
    items = ([discussion.header] if discussion.header is not None else []) + list(discussion.messages)
    for message in items:
        if message.message_id is None or not message.text.strip():
            continue
        text = " ".join(redact(message.text).split())[:_MESSAGE_CHARS]
        if budget - len(text) < 0 and out:
            break
        budget -= len(text)
        ids.add(int(message.message_id))
        out.append({"id": int(message.message_id), "author": message.author or "?",
                    "date": message.datetime.strftime("%Y-%m-%d") if message.datetime else "?",
                    "reply_to": message.reply_to_message_id, "text": text})
    return out, ids


def _verified(payload: dict, ids: set[int]) -> dict:
    """Drop any cited id that is not a message of this discussion."""
    def clean_sources(item):
        if not isinstance(item, dict):
            return None
        sources = []
        for value in item.get("sources") or ():
            try:
                message_id = int(value)
            except (TypeError, ValueError):
                continue
            if message_id in ids:
                sources.append(message_id)
        return {**item, "sources": sources} if sources and isinstance(item.get("text"), str) else None

    out = dict(payload)
    out["answers"] = [a for a in (clean_sources(v) for v in payload.get("answers") or ()) if a]
    out["javan_view"] = clean_sources(payload.get("javan_view"))
    return out


__all__ = ["MIN_AUTHORS", "MIN_MESSAGES", "StudyReport", "pending_discussions", "study_archive"]
