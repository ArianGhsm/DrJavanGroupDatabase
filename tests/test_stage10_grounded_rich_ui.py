from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import parse_qs

from drjavanbot.ai.models import AnswerResult, ClaimSupport, GroundedClaim
from drjavanbot.telegram.api import TelegramAPI, TelegramResponse
from drjavanbot.telegram.config import TelegramConfig
from drjavanbot.telegram.rendering import RichText, answer_chunks, answer_rich_screen, sources_page


class SequenceTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.bodies = []

    def request(self, url, body, timeout):
        self.urls.append(url)
        self.bodies.append(body)
        status, payload = self.responses.pop(0)
        return TelegramResponse(status, json.dumps(payload).encode())


def _answer(*, insufficient=False):
    if insufficient:
        return AnswerResult(
            direct_answer="در پیام‌های گروه، شواهد کافی برای پاسخ به این سؤال پیدا نشد.",
            key_findings=(), disagreements=(), practical_conclusion=None,
            confidence="low", confidence_reason="شواهد قابل استناد کافی در آرشیو گروه پیدا نشد.",
            cited_message_ids=(), source_refs=(), evidence_used_count=0, independent_authors_count=0,
            insufficient_evidence=True, safety_note_if_needed=None,
        )
    support = ClaimSupport(1, "گروه دکتر جوان/messages.html#go_to_message1", "کامپوزیت X خوب بود")
    claim = GroundedClaim("answer", "در پیام گروه، کامپوزیت X خوب بود", (support,))
    return AnswerResult(
        direct_answer=claim.text,
        key_findings=(),
        disagreements=(), practical_conclusion=None,
        confidence="low", confidence_reason="پشتیبانی آرشیوی: 1 پیام از 1 نویسنده مستقل.",
        cited_message_ids=(1,), source_refs=(support.source_ref,), evidence_used_count=1, independent_authors_count=1,
        insufficient_evidence=False, safety_note_if_needed=None, grounded_claims=(claim,),
    )


def test_answer_renderer_visibly_makes_archive_the_only_authority():
    screen = answer_rich_screen(_answer())
    assert "جمع‌بندی پیام‌های گروه" in screen.rich_html
    assert "<blockquote>" in screen.rich_html
    assert "عبارت‌های پشتیبان از گروه" in screen.rich_html
    assert "کامپوزیت X خوب بود" in screen.rich_html
    assert "پیام #1" in screen.rich_html
    assert "منبع پاسخ فقط آرشیو گروه دکتر جوان است" in screen.rich_html
    assert "هوش مصنوعی فقط برای جست‌وجو، انتخاب و چیدمان" in screen.rich_html
    chunks = answer_chunks(_answer())
    assert len(chunks) == 1 and isinstance(chunks[0], RichText)


def test_insufficient_answer_says_not_found_and_never_fills_from_model_knowledge():
    screen = answer_rich_screen(_answer(insufficient=True))
    assert "شواهد کافی" in screen.fallback_html
    assert "دانش عمومی مدل تکمیل نشده" in screen.rich_html


def test_rich_text_uses_send_rich_message_with_rtl_payload():
    transport = SequenceTransport([(200, {"ok": True, "result": {"message_id": 9}})])
    api = TelegramAPI("123:token", transport=transport)
    rich = answer_chunks(_answer())[0]
    api.send_message(42, rich, reply_markup={"inline_keyboard": []})
    assert transport.urls[-1].endswith("/sendRichMessage")
    body = parse_qs(transport.bodies[-1].decode())
    payload = json.loads(body["rich_message"][0])
    assert payload["is_rtl"] is True
    assert "جمع‌بندی پیام‌های گروه" in payload["html"]
    assert "کامپوزیت X خوب بود" in payload["html"]


def test_rich_send_failure_falls_back_to_legacy_html_without_repeating_business_logic():
    transport = SequenceTransport([
        (400, {"ok": False, "error_code": 400}),
        (200, {"ok": True, "result": {"message_id": 9}}),
    ])
    api = TelegramAPI("123:token", transport=transport)
    rich = answer_chunks(_answer())[0]
    api.send_message(42, rich)
    assert transport.urls[0].endswith("/sendRichMessage")
    assert transport.urls[1].endswith("/sendMessage")
    fallback = parse_qs(transport.bodies[1].decode())["text"][0]
    assert fallback == str(rich)
    assert "جمع‌بندی پیام‌های گروه" in fallback
    assert "کامپوزیت X خوب بود" in fallback


def test_rich_ui_can_be_disabled_as_presentation_rollback():
    transport = SequenceTransport([(200, {"ok": True, "result": {"message_id": 9}})])
    api = TelegramAPI("123:token", transport=transport)
    api.rich_ui_enabled = False
    api.send_message(42, answer_chunks(_answer())[0])
    assert transport.urls[-1].endswith("/sendMessage")


def test_source_pagination_is_rich_capable_and_keeps_inline_actions_separate():
    items = [{
        "author": "Dr A",
        "datetime": "2026-01-01",
        "message_id": 1,
        "source_file": "گروه دکتر جوان/messages.html#go_to_message1",
        "text": "کامپوزیت X خوب بود",
    }]
    text, total = sources_page(items, 0)
    assert total == 1 and isinstance(text, RichText)
    assert "پیام‌های منبع" in text.rich_html
    assert "کامپوزیت X خوب بود" in text.rich_html


def test_source_pagination_accepts_object_records_as_well_as_dicts():
    item = SimpleNamespace(
        author="Dr A",
        datetime="2026-01-01",
        message_id=7,
        source_file="گروه دکتر جوان/messages.html",
        source_ref="گروه دکتر جوان/messages.html#go_to_message7",
        text_excerpt="عبارت واقعی گروه",
    )
    text, total = sources_page([item], 0)
    assert total == 1 and "عبارت واقعی گروه" in text.rich_html and "پیام #7" in text.rich_html


def test_rich_ui_env_flag_is_strict(monkeypatch):
    monkeypatch.setenv("DRJAVAN_TG_RICH_UI_ENABLED", "false")
    assert TelegramConfig.from_env().rich_ui_enabled is False
    monkeypatch.setenv("DRJAVAN_TG_RICH_UI_ENABLED", "true")
    assert TelegramConfig.from_env().rich_ui_enabled is True
