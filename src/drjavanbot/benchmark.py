from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import statistics
import tempfile
import time

from drjavanbot.search import SQLiteSearchBackend, SearchQuery
from drjavanbot.storage import full_reindex


@dataclass(frozen=True, slots=True)
class QueryBenchmark:
    query: str
    latency_ms: float
    results: int


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    index_seconds: float
    db_size_bytes: int
    message_count: int
    archive_files: int
    query_results: tuple[QueryBenchmark, ...]
    median_query_ms: float | None

    def as_dict(self) -> dict:
        return asdict(self)


def benchmark_archive(archive_dir: Path, queries: tuple[str, ...]) -> BenchmarkReport:
    with tempfile.TemporaryDirectory(prefix="drjavan-benchmark-") as temp:
        db_path = Path(temp) / "archive.sqlite3"
        index_report = full_reindex(archive_dir, db_path)
        backend = SQLiteSearchBackend(db_path)
        query_results: list[QueryBenchmark] = []
        for query in queries:
            started = time.perf_counter()
            results = backend.search(SearchQuery(raw_query=query, evidence_limit=20))
            elapsed_ms = (time.perf_counter() - started) * 1000
            query_results.append(QueryBenchmark(query, elapsed_ms, len(results)))
        median = statistics.median(item.latency_ms for item in query_results) if query_results else None
        return BenchmarkReport(
            index_seconds=index_report.elapsed_seconds,
            db_size_bytes=index_report.db_size_bytes,
            message_count=index_report.messages,
            archive_files=index_report.archive_files,
            query_results=tuple(query_results),
            median_query_ms=median,
        )
