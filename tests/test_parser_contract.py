from typing import get_type_hints
from unittest import TestCase

from drjavanbot.domain import MessageRecord
from drjavanbot.ingest.contracts import ArchiveFile, TelegramExportParser


class ParserContractTests(TestCase):
    def test_archive_file_carries_incremental_index_fingerprint(self):
        hints = get_type_hints(ArchiveFile)
        self.assertIn("sha256", hints)
        self.assertIn("page_number", hints)
        self.assertIn("size_bytes", hints)

    def test_message_record_has_required_retrieval_and_citation_fields(self):
        required = {
            "message_id", "dom_id", "source_file", "source_page", "source_order",
            "datetime", "author", "text_raw", "text_normalized",
            "reply_to_message_id", "forwarded_from", "links", "media",
            "message_type", "source_locator", "source_sha256", "content_hash",
            "ingest_version",
        }
        self.assertTrue(required.issubset(MessageRecord.__dataclass_fields__.keys()))

    def test_parser_protocol_exposes_parse_file(self):
        self.assertTrue(hasattr(TelegramExportParser, "parse_file"))
