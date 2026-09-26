from pathlib import Path
import sqlite3

from drjavanbot.storage import full_reindex, incremental_index
from drjavanbot.storage.database import connect_database
from drjavanbot.storage.discussions import MAX_CHUNK_MESSAGES
from drjavanbot.storage.migrations import ensure_schema
from conftest import default_message, write_page

DAY1 = "24.04.2026 10:00:00 UTC+03:30"
DAY1_LATER = "24.04.2026 10:05:00 UTC+03:30"
DAY5 = "28.04.2026 10:00:00 UTC+03:30"


def _members(db: Path) -> dict[str, list[int]]:
    con = sqlite3.connect(db)
    out: dict[str, list[int]] = {}
    for key, message_id in con.execute(
        "SELECT d.discussion_key, m.message_id FROM discussions d JOIN discussion_messages dm ON dm.discussion_id=d.id "
        "JOIN messages m ON m.id=dm.message_row_id ORDER BY d.id, dm.position"
    ):
        out.setdefault(key, []).append(message_id)
    con.close()
    return out


def test_reply_tree_and_joined_continuations_form_one_discussion(tmp_path: Path):
    archive = tmp_path / "archive"; archive.mkdir()
    write_page(archive / "messages.html",
        default_message(1, "Asker", "باندینگ نسل هفت برای سمان رزینی؟", date=DAY1)
        + default_message(2, "Other", "موضوع دیگر بدون ریپلای", date=DAY1)
        + default_message(3, "Javan", "برای سمان سلف ادهزیو لازم نیست", reply=1, date=DAY1_LATER)
        + default_message(4, "Javan", "و اچ داخل کانال توصیه نمی‌شود", joined=True, date=DAY1_LATER)
        + default_message(5, "Asker", "ممنون", reply=3, date=DAY1_LATER))
    db = tmp_path / "a.sqlite3"
    full_reindex(archive, db)
    members = _members(db)
    assert members["1"] == [1, 3, 4, 5]
    assert members["2"] == [2]


def test_reply_to_an_old_message_opens_a_new_discussion(tmp_path: Path):
    archive = tmp_path / "archive"; archive.mkdir()
    write_page(archive / "messages.html",
        default_message(1, "A", "سؤال قدیمی", date=DAY1)
        + default_message(2, "B", "جواب همان روز", reply=1, date=DAY1_LATER)
        + default_message(3, "C", "چند روز بعد: یک نکته جدید", reply=1, date=DAY5)
        + default_message(4, "D", "جواب به نکته جدید", reply=3, date=DAY5))
    db = tmp_path / "a.sqlite3"
    full_reindex(archive, db)
    members = _members(db)
    assert members["1"] == [1, 2]
    assert members["3"] == [3, 4]


def test_long_discussions_are_chunked_and_keep_the_opening_question_searchable(tmp_path: Path):
    archive = tmp_path / "archive"; archive.mkdir()
    body = default_message(1, "A", "پرسش اصلی درباره زیرکونیا", date=DAY1)
    body += "".join(default_message(i, f"U{i}", f"پاسخ شماره {i}", reply=1, date=DAY1_LATER) for i in range(2, MAX_CHUNK_MESSAGES + 10))
    write_page(archive / "messages.html", body)
    db = tmp_path / "a.sqlite3"
    full_reindex(archive, db)
    con = sqlite3.connect(db)
    rows = con.execute("SELECT discussion_key, message_count, text_normalized FROM discussions ORDER BY id").fetchall()
    assert [row[0] for row in rows] == ["1.1", "1.2"]
    assert rows[0][1] == MAX_CHUNK_MESSAGES
    assert "زیرکونیا" in rows[1][2]  # header carried into later chunks
    hits = con.execute("SELECT count(*) FROM discussions_fts WHERE discussions_fts MATCH 'زیرکونیا'").fetchone()[0]
    assert hits == 2


def test_incremental_index_rebuilds_discussions(tmp_path: Path):
    archive = tmp_path / "archive"; archive.mkdir()
    write_page(archive / "messages.html", default_message(1, "A", "سؤال", date=DAY1))
    db = tmp_path / "a.sqlite3"
    full_reindex(archive, db)
    write_page(archive / "messages2.html", default_message(2, "B", "جواب", reply=1, date=DAY1_LATER))
    incremental_index(archive, db)
    assert _members(db)["1"] == [1, 2]


def test_schema_v2_database_migrates_and_gets_discussions(tmp_path: Path):
    archive = tmp_path / "archive"; archive.mkdir()
    write_page(archive / "messages.html",
        default_message(1, "A", "سؤال", date=DAY1) + default_message(2, "B", "جواب", reply=1, date=DAY1_LATER))
    db = tmp_path / "a.sqlite3"
    full_reindex(archive, db)
    con = sqlite3.connect(db)
    con.executescript("DROP TABLE discussions_fts; DROP TABLE discussion_messages; DROP TABLE discussions;"
                      "UPDATE schema_meta SET value='2' WHERE key='schema_version';")
    con.commit(); con.close()
    connection = connect_database(db)
    assert ensure_schema(connection) == 3
    connection.close()
    assert _members(db)["1"] == [1, 2]
