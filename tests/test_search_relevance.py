"""Regressions for real archive search failures.

Each case reproduces a question that previously returned nothing or ranked
unrelated messages first on the full Dr Javan archive.
"""
from pathlib import Path

import pytest

from drjavanbot.ai.planner import deterministic_fallback_plan
from drjavanbot.ai.retrieval import retrieve_with_plan
from drjavanbot.ai.search_policy import infer_question_facets
from drjavanbot.intelligence.facets import detect_facets
from drjavanbot.search import SearchQuery, SQLiteSearchBackend
from drjavanbot.search.numerals import colloquial_variants, number_variants
from drjavanbot.search.terms import informative_query
from drjavanbot.storage import full_reindex
from conftest import default_message, write_page


@pytest.fixture
def archive_db(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    noise = "".join(
        default_message(10 + i, f"User {i}", text)
        for i, text in enumerate((
            "پند هفتم مولانا",
            "سمینار پنجم لغایت هفتم دی ماه",
            "کسی میدونه این چه برندی میتونه باشه",
            "قیمتش چنده؟",
            "يعني يا ايمپلنت يا هيچ",
            "نظر دکتر درباره این کیس چیه",
            "کسی تجربه دارید",
        ))
    )
    write_page(
        archive / "messages.html",
        noise
        + default_message(100, "Dr A", "كاربرد باندينگ نسل ٦ و ٧ كه فرمودين پس چيه؟")
        + default_message(101, "Dr B", "برای کامپوزیت خلفی چه برندی رو پیشنهاد میکنید؟")
        + default_message(102, "Dr C", "برند یونیت خوب در حدود قیمت ۳۰۰ تومان")
        + default_message(103, "مهدی جوان", "پالپوتومی با MTA در دندون شیری نتیجه خوبی دارد")
        + default_message(104, "Dr D", "ایمپلنت های ساخت کره و برند اروپایی را مقایسه کنید"),
    )
    db = tmp_path / "archive.sqlite3"
    full_reindex(archive, db)
    return db


def _top(backend: SQLiteSearchBackend, query: str) -> int | None:
    results = backend.search(SearchQuery(raw_query=query, evidence_limit=5, include_context=False))
    return results[0].message.message_id if results else None


def test_conversational_filler_never_becomes_a_search_topic():
    assert informative_query("کسی میدونه قیمت یونیت چنده") == "قیمت یونیت"
    assert informative_query("کامپوزیت خوب برای خلفی چی پیشنهاد میدید") == "کامپوزیت خلفی پیشنهاد"
    assert informative_query("نظر دکتر جوان درباره پالپوتومی با MTA چیه") == "پالپوتومی mta"
    # "جوان" is only dropped as the host's name, not as an adjective.
    assert "جوان" in informative_query("بیمار جوان با درد")


def test_numeral_and_colloquial_equivalents():
    assert "7" in number_variants("هفتم")
    assert "هفتم" in number_variants("7")
    assert number_variants("نه") == ()  # "no" is not a number
    assert colloquial_variants("دندان") == ("دندون",)
    assert colloquial_variants("دندون") == ("دندان",)


@pytest.mark.parametrize(("query", "expected"), [
    ("باندینگ نسل هفتم", 100),
    ("کامپوزیت خوب برای خلفی چی پیشنهاد میدید", 101),
    ("کسی میدونه قیمت یونیت چنده", 102),
    ("نظر دکتر جوان درباره پالپوتومی با MTA چیه", 103),
    ("پالپوتومی دندان شیری", 103),
    ("بهترین برند ایمپلنت چیه", 104),
])
def test_natural_questions_rank_the_relevant_message_first(archive_db: Path, query: str, expected: int):
    assert _top(SQLiteSearchBackend(archive_db), query) == expected


@pytest.mark.parametrize(("query", "expected"), [
    ("کامپوزیت خوب برای خلفی چی پیشنهاد میدید", 101),
    ("کسی میدونه قیمت یونیت چنده", 102),
    ("نظر دکتر جوان درباره پالپوتومی با MTA چیه", 103),
    ("باندینگ نسل هفتم", 100),
])
def test_planned_retrieval_finds_the_discussion(archive_db: Path, query: str, expected: int):
    report = retrieve_with_plan(SQLiteSearchBackend(archive_db), deterministic_fallback_plan(query))
    assert expected in [candidate.message.message_id for candidate in report.candidates[:3]]


def test_copular_chiye_is_not_a_definition_request_when_something_else_is_asked():
    assert "definition" not in infer_question_facets("بهترین برند ایمپلنت چیه")
    assert "definition" not in infer_question_facets("نظر دکتر جوان درباره پالپوتومی چیه")
    assert "definition" in infer_question_facets("پالپوتومی چیه")
    assert "definition" not in detect_facets("نظر گروه درباره پالپوتومی چیه")
    assert "definition" in detect_facets("پالپوتومی چیه")
