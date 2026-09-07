from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from drjavanbot.domain import MessageRecord


class ParseError(RuntimeError):
    """A source file could not be parsed safely."""


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    path: Path
    page_number: int
    sha256: str
    size_bytes: int


class TelegramExportParser(Protocol):
    """Stage-2 parser contract.

    Implementations must be deterministic and must not mutate the source
    archive. A parse failure must be explicit; callers must not commit a
    partially parsed file into the last-known-good index.
    """

    parser_version: str

    def parse_file(self, source: ArchiveFile) -> Iterable[MessageRecord]:
        ...
