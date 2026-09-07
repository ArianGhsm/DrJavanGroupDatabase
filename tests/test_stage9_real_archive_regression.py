from __future__ import annotations

from pathlib import Path
import shutil

from drjavanbot.ai.planner import SearchFamily, SearchPlan, observed_vocabulary
from drjavanbot.ai.retrieval import retrieve_with_plan
from drjavanbot.search import SQLiteSearchBackend
from drjavanbot.storage import full_reindex

ROOT = Path(__file__).resolve().parents[1]
RAW_ARCHIVE = ROOT / "گروه دکتر جوان"


def test_real_archive_composite_semantic_retrieval_regression(tmp_path: Path):
    """Exercise the real Telegram export without modifying or hard-coding answers."""
    selected: list[Path] = []
    for path in sorted(RAW_ARCHIVE.glob("messages*.html"), key=_page_number):
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        if "کامپوزیت" in text or "composite" in text:
            selected.append(path)
            if len(selected) >= 4:
                break
    assert selected, "real archive unexpectedly contains no composite/کامپوزیت page"

    # The canonical discovery contract requires page 1 so joined-author
    # inheritance and logical archive identity stay valid even for a bounded slice.
    page_one = RAW_ARCHIVE / "messages.html"
    to_copy = list(dict.fromkeys((page_one, *selected)))
    isolated = tmp_path / "real-archive-slice"
    isolated.mkdir()
    for source in to_copy:
        shutil.copy2(source, isolated / source.name)

    db_path = tmp_path / "archive.sqlite3"
    report = full_reindex(isolated, db_path)
    assert report.archive_files == len(to_copy) and report.messages > 0

    backend = SQLiteSearchBackend(db_path)
    plan = SearchPlan(
        searchable=True,
        intent="recommendation_comparison",
        core_concepts=("کامپوزیت", "composite"),
        aliases=("composite",),
        optional_concepts=("تجربه", "پیشنهاد", "مقایسه"),
        entity_types=("product_or_brand",),
        query_families=(
            SearchFamily("topic", ("کامپوزیت", "composite")),
            SearchFamily("experience", ("کامپوزیت تجربه", "کامپوزیت پیشنهاد")),
        ),
        phrases=(), exclude_terms=(), low_information_terms=("کدوم", "خوبه"), reply_context=True,
    )
    retrieval = retrieve_with_plan(backend, plan)
    assert retrieval.query_runs >= 2
    assert retrieval.candidates, "semantic retrieval found no real archive evidence for composite"

    evidence_text = " ".join(
        [candidate.message.text_normalized for candidate in retrieval.candidates[:12]]
        + [ctx.text_normalized for candidate in retrieval.candidates[:8] for ctx in candidate.context[:3]]
    ).casefold()
    assert "کامپوزیت" in evidence_text or "composite" in evidence_text

    vocabulary = observed_vocabulary(retrieval.candidates, question="کدوم برند کامپوزیت خوبه؟")
    assert "کدوم" not in vocabulary and "خوبه" not in vocabulary


def _page_number(path: Path) -> int:
    suffix = path.stem.removeprefix("messages")
    return 1 if not suffix else int(suffix)
