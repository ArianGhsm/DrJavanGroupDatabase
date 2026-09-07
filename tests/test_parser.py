from pathlib import Path
import hashlib
import pytest

from drjavanbot.ingest import ArchiveFile, ParseError, TelegramHTMLParser
from drjavanbot.domain import MessageType
from conftest import HTML_HEAD, default_message, service_message, write_page


def test_parser_handles_service_reply_joined_forward_media_links(tmp_path: Path):
    forward = '''
<div class="message default clearfix" id="message7">
 <div class="body">
  <div class="pull_right date details" title="13.07.2017 20:21:54 UTC+03:30">20:21</div>
  <div class="from_name">Samira</div>
  <div class="forwarded body">
   <div class="from_name">Source User <span class="date details" title="13.07.2017 20:21:31 UTC+03:30">13.07.2017</span></div>
   <div class="media_wrap clearfix"><div class="media clearfix pull_left media_photo"><div class="body"><div class="title bold">Photo</div></div></div></div>
   <div class="text">لینک <a href="https://example.com/x">Example</a><br>خط دوم</div>
  </div>
 </div>
</div>'''
    page = write_page(
        tmp_path / "messages.html",
        service_message("message-1", "13 July 2017")
        + default_message(2, "Ziari Babak", "متن اول")
        + default_message(3, "Ziari Babak", "همینه", reply=2, joined=True)
        + forward,
    )
    records = TelegramHTMLParser().parse_file(page)
    assert len(records) == 4
    assert records[0].is_service and records[0].message_type == MessageType.SERVICE
    assert records[2].author == "Ziari Babak"
    assert records[2].reply_to_message_id == 2
    forwarded = records[3]
    assert forwarded.forwarded_from == "Source User"
    assert forwarded.text_raw == "لینک Example\nخط دوم"
    assert forwarded.links[0].href == "https://example.com/x"
    assert forwarded.media[0].kind == "photo"
    assert forwarded.source_locator == f"{tmp_path.name}/messages.html#go_to_message7"


def test_cross_file_reply_target_is_preserved(tmp_path: Path):
    body = '''<div class="message default clearfix" id="message302010"><div class="body">
<div class="pull_right date details" title="24.04.2026 11:45:49 UTC+03:30">11:45</div>
<div class="from_name">F Soltani</div>
<div class="reply_to details">In reply to <a href="messages246.html#go_to_message301977">this message</a></div>
<div class="text">sensibility tests</div></div></div>'''
    page = write_page(tmp_path / "messages247.html", body)
    rec = TelegramHTMLParser().parse_file(page)[0]
    assert rec.reply_to_message_id == 301977
    assert rec.reply_source_file == "messages246.html"


def test_media_only_message_is_media_type(tmp_path: Path):
    body = '''<div class="message default clearfix" id="message9"><div class="body">
<div class="pull_right date details" title="24.04.2026 11:45:49 UTC+03:30">11:45</div><div class="from_name">A</div>
<a class="photo_wrap" href="photos/photo_1.jpg"><div class="media clearfix pull_left media_photo"><div class="title bold">Photo</div></div></a>
</div></div>'''
    page = write_page(tmp_path / "messages.html", body)
    rec = TelegramHTMLParser().parse_file(page)[0]
    assert rec.message_type == MessageType.MEDIA
    assert rec.media[0].path == "photos/photo_1.jpg"


def test_truncated_html_fails_closed(tmp_path: Path):
    path = tmp_path / "messages.html"
    content = HTML_HEAD + default_message(1, "A", "x")
    path.write_text(content, encoding="utf-8")
    source = ArchiveFile(path, 1, hashlib.sha256(content.encode()).hexdigest(), len(content))
    with pytest.raises(ParseError):
        TelegramHTMLParser().parse_file(source)
