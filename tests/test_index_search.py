from pathlib import Path

from drjavanbot.search import SearchQuery, SQLiteSearchBackend
from drjavanbot.storage import database_health, full_reindex, incremental_index
from conftest import default_message, write_page


def test_full_reindex_is_idempotent_and_health_is_good(basic_archive: Path, tmp_path: Path):
    db = tmp_path / "data" / "archive.sqlite3"
    first = full_reindex(basic_archive, db)
    second = full_reindex(basic_archive, db)
    assert first.messages == second.messages == 8
    assert first.archive_files == second.archive_files == 2
    assert database_health(db)["healthy"] is True


def test_exact_fts_synonym_persian_english_and_source_integrity(basic_archive: Path, tmp_path: Path):
    db = tmp_path / "archive.sqlite3"
    full_reindex(basic_archive, db)
    backend = SQLiteSearchBackend(db)

    exact = backend.search(SearchQuery(raw_query="درمان ریشه", evidence_limit=10))
    assert exact
    assert "exact_phrase" in exact[0].match_reasons
    assert exact[0].message.source_locator.endswith("#go_to_message100")

    synonym = backend.search(SearchQuery(raw_query="root canal treatment", evidence_limit=10))
    assert any("rct" in item.message.text_normalized for item in synonym)
    assert any("synonym" in item.match_reasons for item in synonym)

    english = backend.search(SearchQuery(raw_query="e.max", evidence_limit=10))
    assert english and english[0].message.message_id == 200


def test_fuzzy_typo_is_bounded_and_finds_term(basic_archive: Path, tmp_path: Path):
    db = tmp_path / "archive.sqlite3"
    full_reindex(basic_archive, db)
    backend = SQLiteSearchBackend(db)
    results = backend.search(SearchQuery(raw_query="زیرکونیاا", evidence_limit=10))
    assert any(item.message.message_id == 200 for item in results)
    assert any("fuzzy" in item.match_reasons or "synonym" in item.match_reasons for item in results)


def test_reply_and_adaptive_context(basic_archive: Path, tmp_path: Path):
    db = tmp_path / "archive.sqlite3"
    full_reindex(basic_archive, db)
    backend = SQLiteSearchBackend(db)
    result = backend.search(SearchQuery(raw_query="آره", evidence_limit=5))[0]
    assert result.message.message_id == 101
    assert any(ctx.message_id == 100 for ctx in result.context)
    assert all(ctx.source_locator for ctx in result.context)


def test_duplicate_collapse_preserves_independent_author(basic_archive: Path, tmp_path: Path):
    db = tmp_path / "archive.sqlite3"
    full_reindex(basic_archive, db)
    results = SQLiteSearchBackend(db).search(SearchQuery(raw_query="RCT", evidence_limit=20))
    ids = {item.message.message_id for item in results}
    assert 102 in ids or 103 in ids
    assert not ({102, 103} <= ids)
    assert 104 in ids  # same statement by independent author is retained
    assert any(item.cluster_size >= 3 for item in results)


def test_incremental_reindex_skips_unchanged_and_replaces_changed_file(basic_archive: Path, tmp_path: Path):
    db = tmp_path / "archive.sqlite3"
    full_reindex(basic_archive, db)
    no_change = incremental_index(basic_archive, db)
    assert no_change.indexed_files == 0
    assert no_change.skipped_files == 2

    page2 = basic_archive / "messages2.html"
    write_page(page2, default_message(200, "Dr D", "کامپوزیت جدید") + default_message(202, "Dr E", "CBCT لازم است"))
    changed = incremental_index(basic_archive, db)
    assert changed.indexed_files == 1
    backend = SQLiteSearchBackend(db)
    assert backend.search(SearchQuery(raw_query="CBCT"))[0].message.message_id == 202
    assert backend.get_message(201) is None


def test_real_archive_style_npg_emax_query(tmp_path: Path):
    archive = tmp_path / "archive"
    archive.mkdir()
    # Based on the actual messages247.html structure inspected in stage 2.
    actual_style = '''<div class="message default clearfix" id="message302013"><div class="body">
<div class="pull_right date details" title="24.04.2026 11:57:35 UTC+03:30">11:57</div>
<div class="from_name">مهدی جوان</div>
<div class="reply_to details">In reply to <a href="#go_to_message302011">this message</a></div>
<div class="text">استفاده از این آلیاژ‌های زرد رنگ که نان پرشس هستند منعی در رستوریشن ایمکس نداره و حتماً دقت کنید که از خود آلیاژ اصلی npg استفاده بشه</div>
</div></div>'''
    write_page(archive / "messages.html", actual_style)
    db = tmp_path / "archive.sqlite3"
    full_reindex(archive, db)
    result = SQLiteSearchBackend(db).search(SearchQuery(raw_query="NPG e max", evidence_limit=5))[0]
    assert result.message.message_id == 302013
    assert result.message.source_locator == "archive/messages.html#go_to_message302013"


def test_malformed_changed_file_does_not_replace_healthy_index(basic_archive: Path, tmp_path: Path):
    db = tmp_path / "archive.sqlite3"
    full_reindex(basic_archive, db)
    before = SQLiteSearchBackend(db).stats()["messages"]
    page2 = basic_archive / "messages2.html"
    page2.write_text('<html><div class="history"><div class="message default clearfix" id="message999">', encoding="utf-8")
    import pytest
    from drjavanbot.ingest import ParseError
    with pytest.raises(ParseError):
        incremental_index(basic_archive, db)
    assert database_health(db)["healthy"] is True
    assert SQLiteSearchBackend(db).stats()["messages"] == before
    assert SQLiteSearchBackend(db).get_message(201) is not None


def test_joined_author_inherits_across_page_boundary(tmp_path: Path):
    archive = tmp_path / "archive"
    archive.mkdir()
    write_page(archive / "messages.html", default_message(1, "Dr Boundary", "first"))
    write_page(archive / "messages2.html", default_message(2, "ignored", "continuation", joined=True))
    db = tmp_path / "archive.sqlite3"
    full_reindex(archive, db)
    assert SQLiteSearchBackend(db).get_message(2).author == "Dr Boundary"


def test_failed_full_reindex_keeps_existing_database(basic_archive: Path, tmp_path: Path):
    db = tmp_path / "archive.sqlite3"
    full_reindex(basic_archive, db)
    old_hash = db.read_bytes()
    (basic_archive / "messages2.html").write_text('<html><div class="history">broken', encoding="utf-8")
    import pytest
    from drjavanbot.ingest import ParseError
    with pytest.raises(ParseError):
        full_reindex(basic_archive, db)
    assert db.read_bytes() == old_hash
    assert database_health(db)["healthy"] is True
