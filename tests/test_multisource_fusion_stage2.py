from drjavanbot.intelligence.fusion import MultiSourceEvidenceFusion
from drjavanbot.intelligence.models import EvidenceItem, SourceType
from drjavanbot.intelligence.routing import route_sources
from drjavanbot.intelligence.understanding import understand_question


def ev(eid,source,text,trust):
    return EvidenceItem(evidence_id=eid,source_type=source,source_name=str(source),source_ref=eid,text=text,title="odontogenic cyst prevalence",trust_tier="test",trust_score=trust,retrieval_score=.9,independence_key=eid)


def test_scientific_intent_weights_requested_fact_and_authority_above_archive_opinion():
    u=understand_question("شایع ترین کیست ادنتوژنیک چیست؟"); route=route_sources(u)
    archive=ev("a",SourceType.ARCHIVE,"odontogenic cyst: به نظرم رایجه",.42)
    science=ev("s",SourceType.SCIENTIFIC,"Among odontogenic cysts, radicular cyst is the most common; prevalence was 54.6%.",.97)
    ranked=MultiSourceEvidenceFusion().rerank(u,route,(archive,science))
    assert ranked[0].item.evidence_id=="s"
    assert ranked[0].requested_fact_relevance>0
    assert ranked[0].trust_score>ranked[1].trust_score


def test_archive_specific_intent_preserves_archive_priority():
    u=understand_question("گروه درباره کیست اودونتوژنیک چی گفته؟"); route=route_sources(u)
    archive=ev("a",SourceType.ARCHIVE,"کیست اودونتوژنیک در گروه مطرح شد",.42)
    science=ev("s",SourceType.SCIENTIFIC,"odontogenic cyst scientific review",.97)
    ranked=MultiSourceEvidenceFusion().rerank(u,route,(archive,science))
    assert ranked[0].item.evidence_id=="a"
