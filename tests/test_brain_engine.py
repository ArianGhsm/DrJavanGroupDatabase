import json
from pathlib import Path

import pytest

from drjavanbot.ai.cache import ResponseCache
from drjavanbot.brain.engine import ArchiveBrain, Turn
from drjavanbot.brain.retriever import DiscussionRetriever
from drjavanbot.storage import full_reindex
from conftest import default_message, write_page

D1 = "24.04.2026 10:00:00 UTC+03:30"
D2 = "24.04.2026 10:05:00 UTC+03:30"


@pytest.fixture
def archive_db(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"; archive.mkdir()
    write_page(archive / "messages.html",
        default_message(10, "Asker", "روکش زیرکونیوم راکینگ داره، قابل اصلاحه؟", date=D1)
        + default_message(11, "مهدی جوان", "اگر اشکال از تراش باشد قابل اصلاح است وگرنه بهتر است تکرار شود", reply=10, date=D2)
        + default_message(12, "Other", "فریم دورالی امتحان کنید", reply=10, date=D2)
        + default_message(20, "X", "قیمت یونیت دست دوم چنده؟", date=D1)
        + default_message(21, "Y", "حدود سیصد میلیون", reply=20, date=D2))
    db = tmp_path / "a.sqlite3"
    full_reindex(archive, db)
    return db


class ScriptedModel:
    """Plays the three LLM roles deterministically and records what it saw."""

    def __init__(self, *, answer=None, kind="question", standalone=None, pick="راکینگ"):
        self.answer, self.kind, self.standalone, self.pick = answer, kind, standalone, pick
        self.seen: dict[str, dict] = {}

    def complete_json(self, *, stage, system, user, max_tokens):
        payload = json.loads(user)
        self.seen[stage] = payload
        if stage == "understand":
            return {"kind": self.kind, "standalone": self.standalone or payload["message"],
                    "queries": ["راکینگ روکش زیرکونیوم", "روکش لق"], "reply": "سلام!"}
        if stage == "rerank":
            return {"relevant": [{"id": d["id"], "relevance": 3} for d in payload["discussions"] if self.pick in d["opening"]]}
        return self.answer


GOOD = {"answer_found": True, "direct_answer": "به نظر دکتر جوان اگر ایراد از تراش باشد اصلاح‌پذیر است.",
        "points": [{"text": "فریم دورالی برای بررسی پیشنهاد شد", "sources": [12]},
                   {"text": "ادعای ساختگی", "sources": [999]}],
        "javan_view": {"text": "اگر ایراد از تراش باشد قابل اصلاح است وگرنه تکرار شود", "sources": [11]},
        "disagreements": [], "practical_conclusion": "اول تراش را بررسی کنید", "confidence": "high"}


def test_answers_from_the_right_discussion_with_verified_citations(archive_db):
    model = ScriptedModel(answer=GOOD)
    result = ArchiveBrain(retriever=DiscussionRetriever(archive_db), model=model).answer("روکش زیرکونیا لق میزنه چیکار کنم")
    answer = result.answer
    assert not answer.insufficient_evidence
    assert set(answer.cited_message_ids) == {11, 12}          # 999 was never shown → dropped
    assert answer.key_findings[0].startswith("نظر دکتر جوان")
    assert answer.ai_calls == 3
    evidence_ids = {m["id"] for block in model.seen["answer"]["evidence"] for m in block["messages"]}
    assert evidence_ids == {10, 11, 12}                      # only the chosen discussion was shown
    assert all(s.quote for claim in answer.grounded_claims for s in claim.supports)


def test_citations_outside_the_evidence_mean_not_discussed(archive_db):
    fabricated = {**GOOD, "points": [{"text": "x", "sources": [999]}], "javan_view": None}
    answer = ArchiveBrain(retriever=DiscussionRetriever(archive_db), model=ScriptedModel(answer=fabricated)).answer("راکینگ").answer
    assert answer.insufficient_evidence and not answer.cited_message_ids


def test_model_reporting_no_answer_is_respected(archive_db):
    answer = ArchiveBrain(retriever=DiscussionRetriever(archive_db), model=ScriptedModel(answer={"answer_found": False})).answer("راکینگ").answer
    assert answer.insufficient_evidence


def test_no_relevant_discussion_skips_the_answer_call(archive_db):
    model = ScriptedModel(answer=GOOD, pick="چیزی-که-نیست")
    answer = ArchiveBrain(retriever=DiscussionRetriever(archive_db), model=model).answer("روکش").answer
    assert answer.insufficient_evidence and "answer" not in model.seen and answer.ai_calls == 2


def test_smalltalk_is_answered_without_search(archive_db):
    model = ScriptedModel(kind="smalltalk")
    answer = ArchiveBrain(retriever=DiscussionRetriever(archive_db), model=model).answer("سلام").answer
    assert answer.direct_answer == "سلام!" and set(model.seen) == {"understand"}


def test_follow_ups_are_understood_with_history_and_cached_by_meaning(archive_db, tmp_path):
    cache = ResponseCache(tmp_path / "c.sqlite3", ttl_seconds=3600)
    history = [Turn("روکش زیرکونیا لق میزنه", "روکش زیرکونیوم راکینگ دارد، اصلاح‌پذیر است؟", "...")]
    first = ScriptedModel(answer=GOOD, standalone="روکش زیرکونیوم راکینگ دارد؛ مقصر لابراتوار است؟")
    brain = ArchiveBrain(retriever=DiscussionRetriever(archive_db), model=first, cache=cache)
    brain.answer("مقصر کیه؟", history=history)
    assert first.seen["understand"]["conversation"][0]["understood_as"].startswith("روکش زیرکونیوم")
    # Same words, different resolved topic → different cache entry (no cross-topic leak).
    second = ScriptedModel(answer=GOOD, standalone="قیمت یونیت دست دوم؛ مقصر گرانی کیست؟", pick="یونیت")
    other = ArchiveBrain(retriever=DiscussionRetriever(archive_db), model=second, cache=cache).answer("مقصر کیه؟")
    assert not other.answer.cache_hit
    again = ArchiveBrain(retriever=DiscussionRetriever(archive_db), model=ScriptedModel(answer=GOOD, standalone="روکش زیرکونیوم راکینگ دارد؛ مقصر لابراتوار است؟"), cache=cache).answer("مقصر کیه؟", history=history)
    assert again.answer.cache_hit
