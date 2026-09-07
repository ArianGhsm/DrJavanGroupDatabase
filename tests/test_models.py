from datetime import datetime, timezone
from unittest import TestCase

from drjavanbot.domain import MessageRecord, MessageType


class MessageRecordTests(TestCase):
    def test_preserves_dom_id_and_numeric_message_id_separately(self):
        record = MessageRecord(
            message_id=302010,
            dom_id="message302010",
            source_file="گروه دکتر جوان/messages247.html",
            source_page=247,
            source_order=1,
            datetime=datetime(2026, 4, 24, 8, 15, 49, tzinfo=timezone.utc),
            datetime_raw="24.04.2026 11:45:49 UTC+03:30",
            author="F Soltani",
            author_normalized="f soltani",
            text_raw="test",
            text_normalized="test",
            message_type=MessageType.MESSAGE,
            source_locator="گروه دکتر جوان/messages247.html#message302010",
        )
        self.assertEqual(record.message_id, 302010)
        self.assertEqual(record.dom_id, "message302010")
