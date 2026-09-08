from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sys

from drjavanbot.benchmark import benchmark_archive
from drjavanbot.config import Settings
from drjavanbot.health import local_index_health
from drjavanbot.search import SQLiteSearchBackend, SearchQuery
from drjavanbot.semantic_benchmark import semantic_benchmark_archive
from drjavanbot.storage import full_reindex, incremental_index
from drjavanbot.ai.semantic_eval import run_quality_eval
from drjavanbot.ai.eval.reporting import human_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drjavanbot", description="Local archive index and retrieval utilities")
    parser.add_argument("--archive-dir", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("index", help="Incrementally index new/changed Telegram export pages")
    sub.add_parser("reindex", help="Atomically rebuild the entire local index")

    search = sub.add_parser("search", help="Search the local archive index")
    search.add_argument("query")
    search.add_argument("--author")
    search.add_argument("--from", dest="date_from")
    search.add_argument("--to", dest="date_to")
    search.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="Show local index statistics")
    sub.add_parser("health", help="Run SQLite/FTS integrity checks")

    bench = sub.add_parser("benchmark", help="Build an isolated temporary index and benchmark retrieval")
    bench.add_argument("queries", nargs="*", default=["RCT", "ایمپلنت", "e max"])

    semantic = sub.add_parser(
        "semantic-benchmark",
        help="Run deterministic semantic-retrieval regression metrics against the complete archive",
    )
    semantic.add_argument("--top-k", type=int, default=12)
    semantic.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero when stable present/absent retrieval gates fail",
    )

    quality = sub.add_parser(
        "quality-eval",
        help="Run the v2 Quality Lab with one full-archive index and PII-safe reports",
    )
    quality.add_argument("--top-k", type=int, default=12)
    quality.add_argument("--base-sha", default="unknown")
    quality.add_argument("--json-out", type=Path)
    quality.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero when frozen quality/safety gates fail",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env(require_runtime=False)
    archive_dir = args.archive_dir or settings.archive_dir
    db_path = args.db or settings.data_dir / "archive.sqlite3"

    if args.command == "index":
        print(json.dumps(asdict(incremental_index(archive_dir, db_path)), ensure_ascii=False, indent=2))
        return 0
    if args.command == "reindex":
        print(json.dumps(asdict(full_reindex(archive_dir, db_path)), ensure_ascii=False, indent=2))
        return 0
    if args.command == "search":
        backend = SQLiteSearchBackend(db_path)
        query = SearchQuery(
            raw_query=args.query,
            author=args.author,
            date_from=_date(args.date_from),
            date_to=_date(args.date_to),
            evidence_limit=args.limit,
        )
        payload = [_candidate_json(item) for item in backend.search(query)]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "stats":
        print(json.dumps(SQLiteSearchBackend(db_path).stats(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "health":
        print(json.dumps(asdict(local_index_health(db_path)), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "benchmark":
        print(json.dumps(benchmark_archive(archive_dir, tuple(args.queries)).as_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "semantic-benchmark":
        report = semantic_benchmark_archive(archive_dir, top_k=args.top_k)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0 if (not args.strict or report.passed) else 1
    if args.command == "quality-eval":
        report = run_quality_eval(archive_dir, top_k=args.top_k, base_sha=args.base_sha)
        payload = report.as_dict()
        if args.json_out is not None:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(human_summary(report), file=sys.stderr)
        return 0 if (not args.strict or report.passed) else 1
    return 2


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _candidate_json(candidate) -> dict:
    message = candidate.message
    return {
        "message_id": message.message_id,
        "source_file": message.source_file,
        "source_locator": message.source_locator,
        "author": message.author,
        "datetime": message.datetime_raw or (message.datetime.isoformat() if message.datetime else None),
        "text": message.text_raw,
        "local_score": candidate.local_score,
        "matched_terms": candidate.matched_terms,
        "match_reasons": candidate.match_reasons,
        "cluster_key": candidate.cluster_key,
        "cluster_size": candidate.cluster_size,
        "context": [
            {
                "message_id": ctx.message_id,
                "source_locator": ctx.source_locator,
                "author": ctx.author,
                "datetime": ctx.datetime_raw or (ctx.datetime.isoformat() if ctx.datetime else None),
                "text": ctx.text_raw,
            }
            for ctx in candidate.context
        ],
    }
