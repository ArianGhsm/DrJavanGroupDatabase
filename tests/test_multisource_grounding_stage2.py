import json
import pytest
from drjavanbot.intelligence.fusion import RankedEvidence
from drjavanbot.intelligence.grounding import MultiSourceGroundingError,parse_and_validate_grounded_output
from drjavanbot.intelligence.models import EvidenceItem,SourceType


def ranked():
    item=EvidenceItem(evidence_id="pubmed:1",source_type=SourceType.SCIENTIFIC,source_name="J",source_ref="PMID:1",title="Review",text="Radicular cyst was the most common odontogenic cyst in the review.",trust_score=.97,trust_tier="systematic_review")
    return (RankedEvidence(item,1,1,.97,.8,1,.95),)


def test_claim_level_grounding_accepts_exact_support_and_direct_answer_first():
    direct="Radicular cyst was reported as the most common odontogenic cyst."
    payload={"direct_answer":direct,"claims":[{"kind":"answer","text":direct,"supports":[{"evidence_id":"pubmed:1","quote":"Radicular cyst was the most common odontogenic cyst"}]}],"confidence":"high","confidence_reason":"review","disagreements":[],"limitations":[]}
    parsed=parse_and_validate_grounded_output(json.dumps(payload),ranked())
    assert parsed["claims"][0].supports[0].evidence_id=="pubmed:1"


def test_grounding_rejects_unknown_evidence_and_fabricated_quote():
    direct="x"
    for support in ({"evidence_id":"pubmed:999","quote":"Radicular cyst was the most common odontogenic cyst"},{"evidence_id":"pubmed:1","quote":"a sentence that is not present anywhere"}):
        payload={"direct_answer":direct,"claims":[{"kind":"answer","text":direct,"supports":[support]}],"confidence":"low"}
        with pytest.raises(MultiSourceGroundingError): parse_and_validate_grounded_output(json.dumps(payload),ranked())

def test_current_salary_answer_shape_requires_numeric_currency_and_date():
    from drjavanbot.intelligence.synthesis import _validate_requested_answer_shape
    from drjavanbot.intelligence.understanding import understand_question
    from drjavanbot.intelligence.fusion import RankedEvidence
    from drjavanbot.intelligence.models import EvidenceItem, SourceType
    from drjavanbot.intelligence.grounding import MultiSourceGroundingError
    import pytest
    u=understand_question('حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟')
    item=EvidenceItem('x',SourceType.CURRENT_WEB,'src','https://x','حقوق 80 میلیون تومان در 1405',timestamp='2026-01-01T00:00:00+00:00',publication_year=2026,metadata={'current_year_signal':True},trust_score=.8)
    ranked=(RankedEvidence(item,1,1,.8,1,1,.9),)
    with pytest.raises(MultiSourceGroundingError): _validate_requested_answer_shape({'direct_answer':'درآمد می‌تواند متغیر باشد.'},u,ranked)
    _validate_requested_answer_shape({'direct_answer':'بر اساس داده 2026 (۱۴۰۵)، حدود 80 میلیون تومان در ماه گزارش شده است.'},u,ranked)


def test_compact_evidence_is_always_contiguous_verbatim_source_text():
    from drjavanbot.intelligence.synthesis import _compact_item_text
    from drjavanbot.intelligence.models import EvidenceItem, SourceType
    body=("مقدمه "*500)+"در ۱۴۰۵ درآمد تازه کار ۴۰ تا ۹۰ میلیون تومان گزارش شد."+(" ادامه"*500)
    item=EvidenceItem(evidence_id="w",source_type=SourceType.CURRENT_WEB,source_name="x",source_ref="x",text=body,title="عنوان")
    compact=_compact_item_text(item,facets=("salary",))
    full="\n".join((item.title,item.text))
    assert compact in full
    assert "۴۰ تا ۹۰ میلیون تومان" in compact


def test_direct_answer_is_canonicalized_from_first_grounded_claim():
    claim="Radicular cyst was reported as the most common odontogenic cyst."
    payload={"direct_answer":"Different ungrounded presentation text","claims":[{"kind":"answer","text":claim,"supports":[{"evidence_id":"pubmed:1","quote":"Radicular cyst was the most common odontogenic cyst"}]}],"confidence":"high"}
    parsed=parse_and_validate_grounded_output(json.dumps(payload),ranked())
    assert parsed["direct_answer"] == claim

def test_direct_answer_can_be_derived_when_model_omits_redundant_field():
    claim="Radicular cyst was reported as the most common odontogenic cyst."
    payload={"claims":[{"kind":"answer","text":claim,"supports":[{"evidence_id":"pubmed:1","quote":"Radicular cyst was the most common odontogenic cyst"}]}],"confidence":"high"}
    parsed=parse_and_validate_grounded_output(json.dumps(payload),ranked())
    assert parsed["direct_answer"] == claim


def test_compact_support_id_schema_uses_application_owned_verbatim_quote():
    from drjavanbot.intelligence.grounding import SupportSpan
    claim = "Radicular cyst was the most common odontogenic cyst."
    spans = (SupportSpan("s001", "pubmed:1", "Radicular cyst was the most common odontogenic cyst"),)
    payload = {"claims": [{"text": claim, "support_ids": ["s001"]}]}
    parsed = parse_and_validate_grounded_output(json.dumps(payload), ranked(), spans)
    support = parsed["claims"][0].supports[0]
    assert parsed["direct_answer"] == claim
    assert support.evidence_id == "pubmed:1"
    assert support.quote == spans[0].quote
    assert support.support_class == "application_selected_verbatim_span"


def test_compact_support_id_schema_rejects_unknown_id_and_unsupported_number():
    from drjavanbot.intelligence.grounding import SupportSpan
    spans = (SupportSpan("s001", "pubmed:1", "Radicular cyst was the most common odontogenic cyst"),)
    with pytest.raises(MultiSourceGroundingError, match="unknown_support_span"):
        parse_and_validate_grounded_output(json.dumps({"claims": [{"text": "answer", "support_ids": ["s999"]}]}), ranked(), spans)
    with pytest.raises(MultiSourceGroundingError, match="unsupported_numeric_token"):
        parse_and_validate_grounded_output(json.dumps({"claims": [{"text": "It was 80 percent.", "support_ids": ["s001"]}]}), ranked(), spans)


def test_hybrid_claim_order_prefers_scientific_for_scientific_direct_answer():
    from drjavanbot.ai.models import GroundedSourceClaim, SourceSupport
    from drjavanbot.intelligence.synthesis import _canonicalize_claim_order
    from drjavanbot.intelligence.understanding import understand_question
    from drjavanbot.intelligence.routing import route_sources
    u = understand_question('گروه درباره e.max چی گفته و شواهد علمی درباره‌اش چی میگه؟')
    route = route_sources(u)
    a = GroundedSourceClaim('archive','archive', (SourceSupport('a','archive','a','archive quote'),))
    s = GroundedSourceClaim('scientific','science', (SourceSupport('s','scientific','s','science quote'),))
    parsed = _canonicalize_claim_order({'claims': (a,s), 'direct_answer': 'archive'}, u, route)
    assert parsed['claims'][0].supports[0].source_type == 'scientific'


def test_current_presentation_adds_deterministic_uncertainty_without_model_retry():
    from drjavanbot.ai.models import GroundedSourceClaim, SourceSupport
    from drjavanbot.intelligence.synthesis import _canonicalize_presentation
    from drjavanbot.intelligence.understanding import understand_question
    from drjavanbot.intelligence.routing import route_sources
    u = understand_question('حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟')
    route = route_sources(u)
    claim = GroundedSourceClaim('answer','در ۱۴۰۵، 80 میلیون تومان در ماه گزارش شده است.', (SourceSupport('w','current_web','w','در ۱۴۰۵، 80 میلیون تومان در ماه گزارش شده است.'),))
    parsed = _canonicalize_presentation({'claims': (claim,), 'direct_answer': claim.text}, u, route)
    assert 'برآورد' in parsed['direct_answer']
    assert '80 میلیون تومان' in parsed['direct_answer']


def test_numeric_support_is_auto_attached_only_from_same_evidence_item():
    from drjavanbot.intelligence.grounding import SupportSpan, parse_and_validate_grounded_output
    from drjavanbot.intelligence.fusion import RankedEvidence
    item=EvidenceItem(evidence_id='pubmed:1',source_type=SourceType.SCIENTIFIC,source_name='J',source_ref='PMID:1',title='Review',text='Radicular cyst was the most common odontogenic cyst. Radicular cyst accounted for 54.6% of odontogenic cysts.',trust_score=.97,trust_tier='systematic_review')
    local_ranked=(RankedEvidence(item,1,1,.97,.8,1,.95),)
    spans = (
        SupportSpan('s001','pubmed:1','Radicular cyst was the most common odontogenic cyst.'),
        SupportSpan('s002','pubmed:1','Radicular cyst accounted for 54.6% of odontogenic cysts.'),
    )
    payload = {'claims':[{'text':'Radicular cyst was the most common, accounting for 54.6%.','support_ids':['s001']}]}
    parsed = parse_and_validate_grounded_output(json.dumps(payload), local_ranked, spans)
    assert len(parsed['claims'][0].supports) == 2
    assert any('54.6%' in s.quote for s in parsed['claims'][0].supports)


def test_numeric_support_still_rejects_number_absent_from_same_evidence():
    from drjavanbot.intelligence.grounding import SupportSpan
    spans = (SupportSpan('s001','pubmed:1','Radicular cyst was the most common odontogenic cyst.'),)
    payload = {'claims':[{'text':'Radicular cyst was the most common at 99.9%.','support_ids':['s001']}]}
    with pytest.raises(MultiSourceGroundingError, match='unsupported_numeric_token'):
        parse_and_validate_grounded_output(json.dumps(payload), ranked(), spans)


def test_numeric_auto_attach_rejects_unrelated_number_from_same_evidence_item():
    from drjavanbot.intelligence.grounding import SupportSpan
    item=EvidenceItem(
        evidence_id='pubmed:1',source_type=SourceType.SCIENTIFIC,source_name='J',source_ref='PMID:1',title='Review',
        text='Radicular cyst was the most common odontogenic cyst. The study enrolled 50 male participants.',
        trust_score=.97,trust_tier='systematic_review')
    local_ranked=(RankedEvidence(item,1,1,.97,.8,1,.95),)
    spans=(
        SupportSpan('s001','pubmed:1','Radicular cyst was the most common odontogenic cyst.'),
        SupportSpan('s002','pubmed:1','The study enrolled 50 male participants.'),
    )
    payload={'claims':[{'text':'Radicular cyst prevalence was 50%.','support_ids':['s001']}]}
    with pytest.raises(MultiSourceGroundingError, match='unsupported_numeric_token'):
        parse_and_validate_grounded_output(json.dumps(payload), local_ranked, spans)
