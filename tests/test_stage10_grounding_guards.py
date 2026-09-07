from __future__ import annotations

import pytest

from drjavanbot.ai.models import EvidenceMessage, EvidencePack
from drjavanbot.ai.validation import CitationValidationError, validate_answer_payload


def _pack(text: str, *, mid: int = 1) -> EvidencePack:
    return EvidencePack(
        question="کدوم بهتره؟",
        normalized_question="کدوم بهتره",
        budget_name="simple",
        estimated_tokens=100,
        messages=(EvidenceMessage(
            source_ref=f"گروه دکتر جوان/messages.html#go_to_message{mid}",
            message_id=mid,
            author="A",
            datetime=None,
            source_file="گروه دکتر جوان/messages.html",
            text=text,
            role="evidence",
        ),),
    )


def _payload(text: str, quote: str, *, mid: int = 1):
    return {
        "insufficient_evidence": False,
        "claims": [{
            "kind": "answer",
            "text": text,
            "supports": [{"message_id": mid, "quote": quote}],
        }],
    }


def test_number_substring_cannot_authorize_a_different_number():
    pack = _pack("مدل Z250 خوب بود")
    with pytest.raises(CitationValidationError):
        validate_answer_payload(_payload("مدل Z2500 خوب توصیف شده.", "مدل Z250 خوب بود"), pack, question="کدوم بهتره؟")


def test_single_digit_cannot_be_invented():
    pack = _pack("سه لایه گفته شد")
    with pytest.raises(CitationValidationError):
        validate_answer_payload(_payload("در گروه 3 لایه گفته شده.", "سه لایه گفته شد"), pack, question="کدوم بهتره؟")


def test_single_latin_product_component_must_come_from_question_or_quote():
    pack = _pack("max در پیام مطرح شده")
    with pytest.raises(CitationValidationError):
        validate_answer_payload(_payload("e max در گروه مطرح شده.", "max در پیام مطرح شده"), pack, question="کدوم بهتره؟")


def test_exact_model_and_number_tokens_are_allowed_when_present_in_quote():
    pack = _pack("e max مدل 3 خوب توصیف شد")
    answer = validate_answer_payload(
        _payload("e max مدل 3 در گروه خوب توصیف شده.", "e max مدل 3 خوب توصیف شد"),
        pack,
        question="کدوم بهتره؟",
    )
    assert answer.cited_message_ids == (1,)
