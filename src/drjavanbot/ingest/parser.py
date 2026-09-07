from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import json
import re

from bs4 import BeautifulSoup, NavigableString, Tag

from drjavanbot.domain import LinkRef, MediaRef, MessageRecord, MessageType
from drjavanbot.normalization import normalize_author, normalize_text
from .contracts import ArchiveFile, ParseError

_MESSAGE_ID_RE = re.compile(r"^message(\d+)$")
_REPLY_RE = re.compile(r"^(?:(?P<file>[^#]+))?#go_to_message(?P<id>\d+)$")
_MEDIA_CLASSES = {
    "photo", "video", "file", "voice", "audio", "music", "document",
    "sticker", "animation", "contact", "location", "poll", "game", "shop",
}


class TelegramHTMLParser:
    """Robust, deterministic parser for Telegram Desktop HTML exports.

    Memory is bounded to one Telegram export page at a time; callers iterate
    pages instead of loading the full archive into RAM.
    """

    parser_version = "2"

    def parse_file(self, source: ArchiveFile) -> tuple[MessageRecord, ...]:
        try:
            html = source.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ParseError(f"cannot read {source.path}: {exc}") from exc

        # BeautifulSoup deliberately repairs minor HTML defects, but a truncated
        # export page must fail closed instead of being published silently.
        if "</html>" not in html.lower():
            raise ParseError(f"archive page appears truncated (missing </html>): {source.path}")

        soup = BeautifulSoup(html, "html.parser")
        history = soup.find("div", class_="history")
        if history is None:
            raise ParseError(f"Telegram history container not found: {source.path}")
        blocks = history.find_all("div", class_="message", recursive=False)
        if not blocks:
            raise ParseError(f"no Telegram message blocks found: {source.path}")

        records: list[MessageRecord] = []
        seen_dom_ids: set[str] = set()
        last_author: str | None = None
        source_file = source.logical_path or source.path.name

        for source_order, block in enumerate(blocks):
            dom_id = str(block.get("id") or "").strip()
            if not dom_id:
                raise ParseError(f"message block without id in {source.path} at order {source_order}")
            if dom_id in seen_dom_ids:
                raise ParseError(f"duplicate DOM message id {dom_id!r} in {source.path}")
            seen_dom_ids.add(dom_id)

            classes = set(block.get("class") or ())
            is_service = "service" in classes
            is_joined = "joined" in classes
            message_id = _message_id(dom_id)

            if is_service:
                body = _direct_child(block, "div", {"body", "details"}) or block
                text_raw = _extract_text(body)
                links = _extract_links(body)
                record = MessageRecord(
                    message_id=message_id,
                    dom_id=dom_id,
                    source_file=source_file,
                    source_page=source.page_number,
                    source_order=source_order,
                    datetime=None,
                    datetime_raw=None,
                    author=None,
                    author_normalized=None,
                    text_raw=text_raw,
                    text_normalized=normalize_text(text_raw),
                    links=links,
                    message_type=MessageType.SERVICE,
                    is_service=True,
                    is_joined=False,
                    source_locator=_source_locator(source_file, message_id, dom_id),
                    source_sha256=source.sha256,
                    ingest_version=self.parser_version,
                )
                records.append(replace(record, content_hash=content_hash_for(record)))
                continue

            body = _direct_child(block, "div", {"body"})
            if body is None:
                raise ParseError(f"message {dom_id} has no direct body in {source.path}")

            author_node = _direct_child(body, "div", {"from_name"})
            author = _extract_text(author_node).strip() if author_node else None
            if not author and is_joined:
                author = last_author
            if author:
                last_author = author

            date_node = _direct_child(body, "div", {"date", "details"})
            datetime_raw = str(date_node.get("title") or "").strip() if date_node else None
            parsed_datetime = _parse_datetime(datetime_raw)

            reply_to_message_id, reply_source_file = _extract_reply(body, source_file)
            forwarded_body = _find_forwarded_body(body)
            forwarded_from, forwarded_datetime_raw = _extract_forwarded_meta(forwarded_body)

            text_node = _direct_child(body, "div", {"text"})
            if text_node is None and forwarded_body is not None:
                text_node = _direct_child(forwarded_body, "div", {"text"})
            text_raw = _extract_text(text_node)
            text_normalized = normalize_text(text_raw)
            media = _extract_media(body)
            links = _extract_links(body)
            message_type = MessageType.MEDIA if media and not text_normalized else MessageType.MESSAGE

            record = MessageRecord(
                message_id=message_id,
                dom_id=dom_id,
                source_file=source_file,
                source_page=source.page_number,
                source_order=source_order,
                datetime=parsed_datetime,
                datetime_raw=datetime_raw,
                author=author,
                author_normalized=normalize_author(author),
                text_raw=text_raw,
                text_normalized=text_normalized,
                reply_to_message_id=reply_to_message_id,
                reply_source_file=reply_source_file,
                forwarded_from=forwarded_from,
                forwarded_datetime_raw=forwarded_datetime_raw,
                links=links,
                media=media,
                message_type=message_type,
                is_service=False,
                is_joined=is_joined,
                source_locator=_source_locator(source_file, message_id, dom_id),
                source_sha256=source.sha256,
                ingest_version=self.parser_version,
            )
            records.append(replace(record, content_hash=content_hash_for(record)))

        return tuple(records)


def _message_id(dom_id: str) -> int | None:
    match = _MESSAGE_ID_RE.fullmatch(dom_id)
    return int(match.group(1)) if match else None


def _direct_child(parent: Tag, name: str, required_classes: set[str]) -> Tag | None:
    for child in parent.find_all(name, recursive=False):
        classes = set(child.get("class") or ())
        if required_classes.issubset(classes):
            return child
    return None


def _extract_text(node: Tag | None) -> str:
    if node is None:
        return ""
    pieces: list[str] = []
    for descendant in node.descendants:
        if isinstance(descendant, NavigableString):
            pieces.append(str(descendant))
        elif isinstance(descendant, Tag) and descendant.name == "br":
            pieces.append("\n")
    text = "".join(pieces).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _extract_links(node: Tag) -> tuple[LinkRef, ...]:
    out: list[LinkRef] = []
    seen: set[tuple[str, str | None]] = set()
    for anchor in node.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href or "#go_to_message" in href:
            continue
        label = anchor.get_text(" ", strip=True) or None
        key = (href, label)
        if key not in seen:
            seen.add(key)
            out.append(LinkRef(href=href, label=label))
    return tuple(out)


def _extract_reply(body: Tag, source_file: str) -> tuple[int | None, str | None]:
    reply = _direct_child(body, "div", {"reply_to", "details"})
    if reply is None:
        return None, None
    anchor = reply.find("a", href=True)
    if anchor is None:
        return None, None
    href = str(anchor.get("href") or "").strip()
    match = _REPLY_RE.match(href)
    if not match:
        return None, None
    return int(match.group("id")), match.group("file") or source_file


def _find_forwarded_body(body: Tag) -> Tag | None:
    for child in body.find_all("div", recursive=False):
        classes = set(child.get("class") or ())
        if {"forwarded", "body"}.issubset(classes):
            return child
    return None


def _extract_forwarded_meta(forwarded_body: Tag | None) -> tuple[str | None, str | None]:
    if forwarded_body is None:
        return None, None
    from_node = _direct_child(forwarded_body, "div", {"from_name"})
    if from_node is None:
        return None, None
    date_node = from_node.find(class_=lambda value: value and "date" in value if isinstance(value, str) else False)
    forwarded_datetime_raw = None
    if isinstance(date_node, Tag):
        forwarded_datetime_raw = str(date_node.get("title") or date_node.get_text(" ", strip=True) or "").strip() or None
    pieces: list[str] = []
    for child in from_node.contents:
        if isinstance(child, NavigableString):
            pieces.append(str(child))
        elif isinstance(child, Tag) and "date" not in set(child.get("class") or ()):
            pieces.append(child.get_text(" ", strip=True))
    value = " ".join(" ".join(pieces).split()).strip()
    return value or None, forwarded_datetime_raw


def _extract_media(body: Tag) -> tuple[MediaRef, ...]:
    out: list[MediaRef] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for node in body.find_all(True):
        classes = set(node.get("class") or ())
        media_class = next((c for c in classes if c.startswith("media_") and c[6:] in _MEDIA_CLASSES), None)
        if media_class is None:
            continue
        kind = media_class[6:]
        anchor = node if node.name == "a" and node.get("href") else node.find_parent("a", href=True)
        if anchor is None:
            anchor = node.find("a", href=True)
        path = str(anchor.get("href")).strip() if anchor is not None and anchor.get("href") else None
        title = node.find(class_="title")
        description = node.find(class_="description")
        label = _extract_text(title) or _extract_text(description) or None
        key = (kind, path, label)
        if key not in seen:
            seen.add(key)
            out.append(MediaRef(kind=kind, path=path, label=label))
    return tuple(out)


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    cleaned = re.sub(r"\s+UTC(?=[+-]\d{2}:?\d{2}$)", " ", raw.strip())
    for fmt in ("%d.%m.%Y %H:%M:%S %z", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            pass
    return None


def _source_locator(source_file: str, message_id: int | None, dom_id: str) -> str:
    anchor = f"go_to_message{message_id}" if message_id is not None else dom_id
    return f"{source_file}#{anchor}"


def content_hash_for(record: MessageRecord) -> str:
    payload = {
        "message_id": record.message_id,
        "dom_id": record.dom_id,
        "author": record.author_normalized,
        "text": record.text_normalized,
        "reply": record.reply_to_message_id,
        "reply_file": record.reply_source_file,
        "forwarded": normalize_text(record.forwarded_from),
        "forwarded_datetime_raw": record.forwarded_datetime_raw,
        "links": [(item.href, item.label) for item in record.links],
        "media": [(item.kind, item.path, item.label) for item in record.media],
        "type": record.message_type.value,
        "service": record.is_service,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
