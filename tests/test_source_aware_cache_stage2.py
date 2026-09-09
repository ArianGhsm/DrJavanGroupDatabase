from pathlib import Path
from drjavanbot.ai.models import AnswerResult
from drjavanbot.intelligence.cache import SourceAwareResponseCache,cache_key,ttl_for_route
from drjavanbot.intelligence.routing import route_sources
from drjavanbot.intelligence.understanding import understand_question


def answer(mode):
    return AnswerResult(direct_answer="grounded",key_findings=(),disagreements=(),practical_conclusion=None,confidence="low",confidence_reason="test",cited_message_ids=(),source_refs=(),evidence_used_count=1,independent_authors_count=1,insufficient_evidence=False,safety_note_if_needed=None,source_mode=mode)


def test_cache_key_versions_route_and_current_ttl_is_shorter(tmp_path:Path):
    current=understand_question("حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟"); cr=route_sources(current)
    science=understand_question("شایع ترین کیست ادنتوژنیک چیست؟"); sr=route_sources(science)
    k1=cache_key(current,cr,model_signature="m",archive_fingerprint="a")
    k2=cache_key(science,sr,model_signature="m",archive_fingerprint="a")
    assert k1!=k2 and ttl_for_route(current,cr)<ttl_for_route(science,sr)
    cache=SourceAwareResponseCache(tmp_path/"cache.sqlite3"); cache.set(k1,answer("current"),ttl_seconds=3600)
    got=cache.get(k1); assert got and got.cache_hit and got.source_mode=="current"
