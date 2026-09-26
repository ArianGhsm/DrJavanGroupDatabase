from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import pytest

from drjavanbot.ai import AIConfig, ResponseCache, TelemetryStore
from drjavanbot.ai.config import classify_question
from drjavanbot.ai.models import ProviderResult, UsageMetrics
from drjavanbot.ai.validation import CitationValidationError, ModelOutputError, parse_json_object, validate_answer_payload
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate
from drjavanbot.secrets import AVALAI_API_KEY_SECRET


def record(mid: int, author: str, text: str, *, source_file: str = "گروه دکتر جوان/messages247.html") -> MessageRecord:
    return MessageRecord(
        message_id=mid,
        dom_id=f"message{mid}",
        source_file=source_file,
        source_page=247,
        source_order=mid,
        datetime=datetime(2026, 4, 24, tzinfo=timezone.utc),
        datetime_raw="24.04.2026 12:00:00 UTC+03:30",
        author=author,
        author_normalized=author.casefold(),
        text_raw=text,
        text_normalized=text.casefold(),
        source_locator=f"{source_file}#go_to_message{mid}",
    )


def candidate(mid: int, author: str, text: str, score: float = 6.0, reasons=("exact_phrase",), context=()) -> EvidenceCandidate:
    return EvidenceCandidate(record(mid, author, text), score, ("rct",), tuple(reasons), tuple(context))


def supported_answer(*, text="در پیام‌های گروه RCT مطرح شده است.", supports=((1, "RCT"), (2, "RCT")), extra_claims=()):
    return {
        "insufficient_evidence": False,
        "claims": [
            {
                "kind": "answer",
                "text": text,
                "supports": [{"message_id": mid, "quote": quote} for mid, quote in supports],
            },
            *extra_claims,
        ],
    }


def insufficient_answer():
    return {"insufficient_evidence": True, "claims": []}


class MemorySecretStore:
    def __init__(self, value="test-secret-123"):
        self.data = {AVALAI_API_KEY_SECRET: value} if value else {}
    def get_secret(self, name): return self.data.get(name)
    def set_secret(self, name, value): self.data[name] = value
    def delete_secret(self, name): return self.data.pop(name, None) is not None
    def is_configured(self, name): return name in self.data


class MockBackend:
    def __init__(self, initial, expanded=None):
        self.initial = tuple(initial)
        self.expanded = tuple(expanded if expanded is not None else initial)
        self.index_version = "v1"
        self.calls = []
    def search(self, query):
        self.calls.append(query)
        q = query.raw_query.casefold()
        if any(term in q for term in ("درمان ریشه", "root canal", "refined", "corpus hint")):
            return self.expanded
        return self.initial
    def get_message(self, message_id): return None
    def get_context(self, message, **kwargs): return ()
    def stats(self): return {"index_version": self.index_version, "messages": 10}


class MockProvider:
    """Tests auto-supply a compact valid semantic planner before synthesis."""
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []
    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["request_type"] == "search_plan":
            question = json.loads(kwargs["user_prompt"])["question"]
            content = json.dumps({
                "searchable": True,
                "intent": "archive_lookup",
                "core_concepts": [question],
                "aliases": [],
                "optional_concepts": [],
                "entity_types": [],
                "query_families": [
                    {"name": "topic", "queries": [question]},
                    {"name": "context", "queries": [question + " تجربه"]},
                ],
                "phrases": [],
                "exclude_terms": [],
                "low_information_terms": [],
                "reply_context": True,
            }, ensure_ascii=False)
        else:
            content = self.contents.pop(0)
        return ProviderResult(content, "deepseek-v4-flash", UsageMetrics(100, 20, 30, 130, 12.5, "IRT", 1.0), 12.0)


def test_default_token_budgets_are_bounded():
    cfg = AIConfig()
    assert cfg.budget_for("RCT چیه").max_evidence_tokens == 2500
    assert cfg.budget_for("RCT چیه").max_output_tokens >= 800
    assert cfg.planner_max_output_tokens < cfg.medium_output_tokens
    b = cfg.budget_for("بهترین روش را مقایسه کن و اختلاف نظرات و تجربه ها و مزایا و معایب را کامل جمع بندی کن")
    assert b.max_evidence_tokens <= cfg.hard_evidence_tokens and b.max_messages <= cfg.hard_messages


def test_colloquial_kodom_is_not_misclassified_as_simple_lookup():
    assert classify_question("کدوم برند کامپوزیت خوبه؟") == "medium"


























def test_malformed_model_json_fails_closed():
    with pytest.raises(ModelOutputError):
        parse_json_object("not { valid")






















