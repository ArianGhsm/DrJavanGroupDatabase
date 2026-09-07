from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .contracts import ArchiveFile, ParseError

_MESSAGE_FILE_RE = re.compile(r"^messages(?:(\d+))?\.html$", re.IGNORECASE)


def page_number_for(path: Path) -> int:
    match = _MESSAGE_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"not a Telegram messages file: {path.name}")
    suffix = match.group(1)
    if suffix is None:
        return 1
    page = int(suffix)
    if page < 2:
        raise ValueError(f"unexpected Telegram page filename: {path.name}")
    return page


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def discover_archive_files(archive_dir: Path) -> tuple[ArchiveFile, ...]:
    if not archive_dir.is_dir():
        raise ParseError(f"archive directory does not exist: {archive_dir}")
    found: list[ArchiveFile] = []
    seen_pages: set[int] = set()
    for path in archive_dir.iterdir():
        if not path.is_file() or not _MESSAGE_FILE_RE.match(path.name):
            continue
        page = page_number_for(path)
        if page in seen_pages:
            raise ParseError(f"duplicate archive page number: {page}")
        seen_pages.add(page)
        stat = path.stat()
        logical_path = path.relative_to(archive_dir.parent).as_posix()
        found.append(ArchiveFile(path, page, sha256_file(path), stat.st_size, logical_path))
    found.sort(key=lambda item: item.page_number)
    if not found:
        raise ParseError(f"no messages*.html files found in {archive_dir}")
    if found[0].page_number != 1:
        raise ParseError("messages.html (page 1) is missing")
    return tuple(found)
