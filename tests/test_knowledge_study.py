import json
from pathlib import Path
import threading

import pytest

from drjavanbot.ai.provider import AuthenticationError
from drjavanbot.brain.retriever import DiscussionRetriever
from drjavanbot.knowledge.store import KnowledgeStore
from drjavanbot.knowledge.study import pending_discussions, study_archive
from drjavanbot.storage import full_reindex
from conftest import default_message, write_page

D1 = "24.04.2026 10:00:00 UTC+03:30"
D2 = "24.04.2026 10:05:00 UTC+03:30"


def _archive(tmp_path: Path, *, extra: str = "") -> tuple[Path, Path]:
    archive = tmp_path / "archive"; archive.mkdir(exist_ok=True)
    write_page(archive / "messages.html",
        default_message(10, "A", "روکش زیرکونیوم راکینگ داره", date=D1)
        + default_message(11, "مهدی جوان", "اگر از تراش است اصلاح کنید", reply=10, date=D2)
        + default_message(12, "B", "فریم دورالی امتحان کنید", reply=10, date=D2)
        + default_message(20, "C", "سلام صبح بخیر", date=D1)
        + default_message(21, "D", "صبح بخیر", reply=20, date=D2)
        + default_message(22, "E", "🌹", reply=20, date=D2) + extra)
    db = tmp_path / "a.sqlite3"
    full_reindex(archive, db)
    return archive, db


class FakeStudent:
    def __init__(self):
        self.calls = 0

    def complete_json(self, *, stage, system, user, max_tokens):
        assert stage == "study"
        self.calls += 1
        messages = json.loads(user)["messages"]
        if "زیرکونیوم" not in messages[0]["text"]:
            return {"useful": False}
        return {"useful": True, "topic": "راکینگ روکش زیرکونیوم", "question": "روکش لق است",
                "summary": "دکتر جوان گفت اگر ایراد از تراش است اصلاح شود.",
                "answers": [{"text": "فریم دورالی", "sources": [12, 999]}],
                "javan_view": {"text": "اصلاح تراش", "sources": [11]},
                "keywords": ["لق", "لقی روکش", "rocking", "zirconia crown"]}


def test_cards_are_written_verified_and_only_once(tmp_path):
    _, db = _archive(tmp_path)
    store = KnowledgeStore(tmp_path / "k.sqlite3")
    student = FakeStudent()
    report = study_archive(db, store, student, workers=2)
    assert (report.studied, report.useful, report.failed) == (2, 1, 0)
    card = next(iter(store.get_many(["10"]).values()))
    assert card.payload["answers"][0]["sources"] == [12]        # 999 is not in this discussion
    assert card.javan_view == "اصلاح تراش"
    again = study_archive(db, store, student)
    assert again.pending == 0 and student.calls == 2             # nothing re-studied


def test_changed_discussion_is_studied_again(tmp_path):
    archive, db = _archive(tmp_path)
    store = KnowledgeStore(tmp_path / "k.sqlite3")
    study_archive(db, store, FakeStudent())
    _archive(tmp_path, extra=default_message(13, "F", "من هم موافقم", reply=10, date=D2))
    assert [key for _, key, _ in pending_discussions(db, store)] == ["10"]


def test_card_keywords_make_paraphrases_findable(tmp_path):
    _, db = _archive(tmp_path)
    plain = DiscussionRetriever(db)
    store = KnowledgeStore(tmp_path / "k.sqlite3")
    study_archive(db, store, FakeStudent())
    studied = DiscussionRetriever(db, knowledge=store)
    top = studied.load(studied.search(["لقی روکش"])[:1])
    assert top and top[0].key == "10" and top[0].card.topic == "راکینگ روکش زیرکونیوم"
    assert not plain.search(["لقی"])                            # the word never occurs in the archive


def test_authentication_failure_stops_the_study(tmp_path):
    _, db = _archive(tmp_path)

    class Broken:
        def complete_json(self, **kwargs):
            raise AuthenticationError("bad key")

    report = study_archive(db, KnowledgeStore(tmp_path / "k.sqlite3"), Broken(), workers=1)
    assert report.stopped_reason == "authentication_failed" and report.studied == 0
