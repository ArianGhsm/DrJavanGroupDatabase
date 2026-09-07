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
        validate_answer_payload(_payload("مدل Z2500 خوب بود", "مدل Z250 خوب بود"), pack, question="کدوم بهتره؟")


def test_single_digit_cannot_be_invented():
    pack = _pack("سه لایه گفته شد")
    with pytest.raises(CitationValidationError):
        validate_answer_payload(_payload("در گروه 3 لایه گفته شد", "سه لایه گفته شد"), pack, question="کدوم بهتره؟")


def test_single_latin_product_component_must_come_from_quote():
    pack = _pack("max در پیام مطرح شده")
    with pytest.raises(CitationValidationError):
        validate_answer_payload(_payload("e max در گروه مطرح شده", "max در پیام مطرح شده"), pack, question="e max خوبه؟")


def test_question_itself_is_not_evidence_for_product_name():
    pack = _pack("خیلی خوب بود")
    with pytest.raises(CitationValidationError):
        validate_answer_payload(
            _payload("در گروه Filtek خیلی خوب بود", "خیلی خوب بود"),
            pack,
            question="Filtek خوبه؟",
        )


def test_substantive_best_claim_cannot_be_added_to_thin_quote():
    pack = _pack("RCT مطرح شد")
    with pytest.raises(CitationValidationError):
        validate_answer_payload(
            _payload("در گروه RCT بهترین درمان مطرح شده است", "RCT مطرح شد"),
            pack,
            question="بهترین درمان چیه؟",
        )


def test_substantive_recommendation_word_must_be_in_support():
    pack = _pack("کامپوزیت X خوب بود")
    with pytest.raises(CitationValidationError):
        validate_answer_payload(
            _payload("در گروه کامپوزیت X پیشنهاد شده است", "کامپوزیت X خوب بود"),
            pack,
            question="چی پیشنهاد میشه؟",
        )


def test_generic_framing_without_archive_content_is_rejected():
    pack = _pack("RCT")
    with pytest.raises(CitationValidationError):
        validate_answer_payload(
            _payload("در پیام گروه مطرح شده است", "RCT"),
            pack,
            question="RCT؟",
        )


def test_exact_model_number_and_substantive_words_pass_when_in_quote():
    pack = _pack("e max مدل 3 خوب توصیف شد")
    answer = validate_answer_payload(
        _payload("e max مدل 3 در گروه خوب توصیف شده است", "e max مدل 3 خوب توصیف شد"),
        pack,
        question="کدوم بهتره؟",
    )
    assert answer.cited_message_ids == (1,)


def test_near_extractive_archive_wording_passes():
    pack = _pack("Filtek خیلی خوب بود و پیشنهادش کردم")
    answer = validate_answer_payload(
        _payload(
            "در پیام گروه Filtek خیلی خوب بود و پیشنهادش کردم",
            "Filtek خیلی خوب بود و پیشنهادش کردم",
        ),
        pack,
        question="کدوم برند کامپوزیت خوبه؟",
    )
    assert answer.direct_answer.endswith("پیشنهادش کردم")
