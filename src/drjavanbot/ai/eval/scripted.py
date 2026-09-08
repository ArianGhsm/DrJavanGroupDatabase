from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.models import ProviderResult, UsageMetrics
from drjavanbot.ai.orchestrator import ArchiveAnswerService
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate
from drjavanbot.secrets import AVALAI_API_KEY_SECRET
from .schema import ScriptedMetrics


class _SecretStore:
    def get_secret(self, name):
        return "quality-lab-secret" if name == AVALAI_API_KEY_SECRET else None
    def set_secret(self, name, value):
        return None
    def delete_secret(self, name):
        return False
    def is_configured(self, name):
        return name == AVALAI_API_KEY_SECRET


def _record(mid: int, author: str, text: str) -> MessageRecord:
    return MessageRecord(
        message_id=mid,
        dom_id=f"message{mid}",
        source_file="synthetic/messages.html",
        source_page=1,
        source_order=mid,
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime_raw="synthetic",
        author=author,
        author_normalized=author.casefold(),
        text_raw=text,
        text_normalized=text.casefold(),
        source_locator=f"synthetic/messages.html#go_to_message{mid}",
    )


def _candidate(mid: int, author: str, text: str, *, context=()) -> EvidenceCandidate:
    return EvidenceCandidate(
        message=_record(mid, author, text),
        local_score=8.0,
        matched_terms=("synthetic",),
        match_reasons=("exact_phrase", "normalized_tokens"),
        context=tuple(context),
    )


class _Backend:
    def __init__(self, candidates, *, hydrate=()):
        self.candidates = tuple(candidates)
        self.hydrate = tuple(hydrate)
        self.calls = []
    def search(self, query):
        self.calls.append(query.raw_query)
        return self.candidates
    def get_message(self, message_id):
        for item in self.candidates:
            if item.message.message_id == message_id:
                return item.message
        return None
    def get_context(self, message, **kwargs):
        return self.hydrate
    def stats(self):
        return {"index_version": "quality-lab-synthetic", "messages": len(self.candidates)}


@dataclass
class _Provider:
    mode: str
    claim_text: str = "RCT مطرح شد"
    quote: str = "RCT مطرح شد"

    def __post_init__(self):
        self.calls: list[dict] = []

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        kind = kwargs["request_type"]
        if kind == "search_plan":
            question = json.loads(kwargs["user_prompt"])["question"]
            required = ["topic", "timing_age"] if self.mode == "facet_recheck" else []
            content = json.dumps({
                "searchable": True,
                "intent": "timing_age" if required else "archive_lookup",
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
                "required_aspects": required,
            }, ensure_ascii=False)
        elif kind == "search_refinement":
            content = json.dumps({"query_families": [{"name": "refined", "queries": ["synthetic refined"]}]})
        elif kind in {"synthesis", "synthesis_recheck"}:
            if self.mode == "malformed":
                content = "{bad json"
            elif self.mode == "facet_recheck" and kind == "synthesis":
                content = json.dumps({"insufficient_evidence": True, "claims": []})
            else:
                content = json.dumps({
                    "insufficient_evidence": False,
                    "claims": [{
                        "kind": "answer",
                        "text": self.claim_text,
                        "supports": [{"message_id": 1, "quote": self.quote}],
                    }],
                }, ensure_ascii=False)
        else:
            content = json.dumps({"query_families": []})
        usage = UsageMetrics(100, 20, 30, 130, 12.5, "IRT", 1.0)
        return ProviderResult(content, "scripted-provider", usage, 1.0)


def run_scripted_e2e() -> ScriptedMetrics:
    failures: list[str] = []
    logical_calls: dict[str, int] = {}
    token_budget: dict[str, int] = {}
    answerable_total = false_insufficient = absent_total = false_supported = 0
    reason_expected = reason_correct = fragmented_total = fragmented_recovered = 0

    def run(name, backend, provider, question, *, answerable, expect_insufficient, fragmented=False, expect_reason=None):
        nonlocal answerable_total, false_insufficient, absent_total, false_supported
        nonlocal reason_expected, reason_correct, fragmented_total, fragmented_recovered
        events: list[tuple[str, dict]] = []
        answer = ArchiveAnswerService(
            backend=backend,
            secret_store=_SecretStore(),
            config=AIConfig(),
            provider=provider,
        ).answer(question, progress=lambda stage, details: events.append((stage, details)))
        logical_calls[name] = answer.ai_calls
        token_budget[name] = 130 * len(provider.calls)
        if answerable:
            answerable_total += 1
            false_insufficient += int(answer.insufficient_evidence)
        else:
            absent_total += 1
            false_supported += int(not answer.insufficient_evidence)
        if fragmented:
            fragmented_total += 1
            fragmented_recovered += int(not answer.insufficient_evidence)
        if expect_reason is not None:
            reason_expected += 1
            reason_correct += int(any(stage == expect_reason for stage, _ in events))
        if answer.insufficient_evidence != expect_insufficient:
            failures.append(name)

    direct = (_candidate(1, "A", "RCT مطرح شد"), _candidate(2, "B", "RCT مطرح شد"))
    run("answerable_direct", _Backend(direct), _Provider("normal"), "RCT", answerable=True, expect_insufficient=False)

    parent = _record(10, "A", "زمینه بحث مصنوعی")
    fragmented = (_candidate(1, "A", "RCT مطرح شد", context=(parent,)), _candidate(2, "B", "RCT مطرح شد", context=(parent,)))
    run("answerable_fragmented", _Backend(fragmented, hydrate=(parent,)), _Provider("normal"), "RCT", answerable=True, expect_insufficient=False, fragmented=True)

    run("known_absent", _Backend(()), _Provider("normal"), "zzqv9f7b6a21drjx", answerable=False, expect_insufficient=True, expect_reason="no_evidence")

    facet_candidates = (_candidate(1, "A", "موضوع مصنوعی سن 8 مطرح شد"), _candidate(2, "B", "موضوع مصنوعی سن 8 مطرح شد"))
    run(
        "facet_false_insufficient_recheck",
        _Backend(facet_candidates),
        _Provider("facet_recheck", claim_text="موضوع مصنوعی سن 8 مطرح شد", quote="موضوع مصنوعی سن 8 مطرح شد"),
        "زمان موضوع مصنوعی؟",
        answerable=True,
        expect_insufficient=False,
        expect_reason="repairing",
    )

    run("malformed_provider", _Backend(direct), _Provider("malformed"), "RCT", answerable=False, expect_insufficient=True, expect_reason="validation_failed")

    total = 5
    return ScriptedMetrics(
        total=total,
        passed=total - len(failures),
        false_insufficient_rate=round(false_insufficient / answerable_total, 4) if answerable_total else 0.0,
        false_supported_rate=round(false_supported / absent_total, 4) if absent_total else 0.0,
        reason_code_accuracy=round(reason_correct / reason_expected, 4) if reason_expected else 1.0,
        answerable_fragmented_recovery_rate=round(fragmented_recovered / fragmented_total, 4) if fragmented_total else 1.0,
        logical_calls_by_case=logical_calls,
        token_budget_by_case=token_budget,
        failures=tuple(failures),
    )
