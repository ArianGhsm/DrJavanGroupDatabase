from datetime import datetime, timezone
from pathlib import Path

from drjavanbot.benchmark import benchmark_archive
from drjavanbot.search import SearchQuery, SQLiteSearchBackend
from drjavanbot.storage import full_reindex


def test_author_date_filter_stats_and_benchmark(basic_archive: Path, tmp_path: Path):
    db = tmp_path / "archive.sqlite3"
    full_reindex(basic_archive, db)
    backend = SQLiteSearchBackend(db)
    filtered = backend.search(SearchQuery(
        raw_query="RCT",
        author="Dr C",
        date_from=datetime(2026, 4, 24, tzinfo=timezone.utc),
        evidence_limit=10,
    ))
    assert [item.message.message_id for item in filtered] == [104]
    stats = backend.stats()
    assert stats["archive_files"] == 2
    assert stats["messages"] == 8
    assert stats["authors"] >= 4

    bench = benchmark_archive(basic_archive, ("RCT", "e max"))
    assert bench.message_count == 8
    assert bench.db_size_bytes > 0
    assert len(bench.query_results) == 2
    assert bench.median_query_ms is not None
