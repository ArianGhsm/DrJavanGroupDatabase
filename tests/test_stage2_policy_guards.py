from drjavanbot.intelligence.current_provider import _public_http_url
from drjavanbot.intelligence.routing import route_sources
from drjavanbot.intelligence.understanding import understand_question
from drjavanbot.intelligence.models import SourceType

def test_archive_only_override_blocks_external_sources():
    r=route_sources(understand_question('فقط از گروه درباره e.max و شواهد علمی بگو'))
    assert r.required_sources==(SourceType.ARCHIVE,)
    assert [x.source_type for x in r.selected_sources]==[SourceType.ARCHIVE]

def test_new_guideline_is_recent_scientific_not_current_market():
    u=understand_question('guideline جدید antibiotic prophylaxis در دندانپزشکی چیه؟')
    r=route_sources(u)
    assert str(u.freshness)=='recent'
    assert r.selected_sources[0].source_type==SourceType.SCIENTIFIC
    assert SourceType.CURRENT_WEB not in r.required_sources

def test_public_url_guard_rejects_ssrf_targets():
    assert not _public_http_url('http://127.0.0.1/x')
    assert not _public_http_url('http://169.254.169.254/latest/meta-data')
    assert not _public_http_url('http://localhost/x')
    assert _public_http_url('https://example.com/x')
