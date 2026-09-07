from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class MessageType(StrEnum):
    MESSAGE = "message"
    SERVICE = "service"
    MEDIA = "media"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LinkRef:
    href: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class MediaRef:
    kind: str
    path: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class MessageRecord:
    """Loss-minimizing representation of one Telegram-export message block."""

    message_id: int | None
    dom_id: str
    source_file: str
    source_page: int
    source_order: int
    datetime: datetime | None
    datetime_raw: str | None
    author: str | None
    author_normalized: str | None
    text_raw: str
    text_normalized: str
    reply_to_message_id: int | None = None
    reply_source_file: str | None = None
    forwarded_from: str | None = None
    forwarded_datetime_raw: str | None = None
    links: tuple[LinkRef, ...] = field(default_factory=tuple)
    media: tuple[MediaRef, ...] = field(default_factory=tuple)
    message_type: MessageType = MessageType.MESSAGE
    is_service: bool = False
    is_joined: bool = False
    source_locator: str = ""
    source_sha256: str = ""
    content_hash: str = ""
    ingest_version: str = "1"
