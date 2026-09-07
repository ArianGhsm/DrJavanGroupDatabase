from __future__ import annotations

from pathlib import Path
import hashlib

import pytest

from drjavanbot.ingest.contracts import ArchiveFile


HTML_HEAD = '''<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><div class="history">'''
HTML_TAIL = '''</div></body></html>'''


def default_message(mid: int, author: str, text: str, *, reply: int | None = None, joined: bool = False, date: str = "24.04.2026 11:59:58 UTC+03:30") -> str:
    reply_html = f'<div class="reply_to details">In reply to <a href="#go_to_message{reply}">this message</a></div>' if reply else ''
    author_html = '' if joined else f'<div class="from_name">{author}</div>'
    joined_class = ' joined' if joined else ''
    return f'''<div class="message default clearfix{joined_class}" id="message{mid}"><div class="body"><div class="pull_right date details" title="{date}">11:59</div>{author_html}{reply_html}<div class="text">{text}</div></div></div>'''


def service_message(dom: str, text: str) -> str:
    return f'<div class="message service" id="{dom}"><div class="body details">{text}</div></div>'


def write_page(path: Path, body: str) -> ArchiveFile:
    content = HTML_HEAD + body + HTML_TAIL
    path.write_text(content, encoding="utf-8")
    raw = content.encode()
    return ArchiveFile(path=path, page_number=1 if path.name == "messages.html" else int(path.stem.removeprefix("messages")), sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), logical_path=f"{path.parent.name}/{path.name}")


@pytest.fixture
def basic_archive(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    write_page(
        archive / "messages.html",
        service_message("message-1", "24 April 2026")
        + default_message(100, "Dr A", "درمان ریشه با RCT مناسب است")
        + default_message(101, "Dr B", "آره", reply=100)
        + default_message(102, "Dr B", "درمان ریشه با RCT مناسب است")
        + default_message(103, "Dr B", "درمان ریشه با RCT مناسب است")
        + default_message(104, "Dr C", "درمان ریشه با RCT مناسب است"),
    )
    write_page(
        archive / "messages2.html",
        default_message(200, "Dr D", "برای e max و زیرکونیا باید اپاسیته مناسب انتخاب شود")
        + default_message(201, "Dr D", "MTA plug برای open apex مطرح است"),
    )
    return archive
