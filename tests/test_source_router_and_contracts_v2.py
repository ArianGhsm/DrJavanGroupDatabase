from drjavanbot.intelligence.core import DentalIntelligenceCore
from drjavanbot.intelligence.models import SourceType
from drjavanbot.intelligence.query_generation import generate_retrieval_requests
from drjavanbot.intelligence.retrieval import RetrievalRegistry, UnavailableRetrievalProvider
from drjavanbot.intelligence.routing import route_sources
from drjavanbot.intelligence.understanding import understand_question


def test_source_router_covers_archive_science_current_and_hybrid_contracts():
    archive = route_sources(understand_question("گروه درباره e.max چی گفته؟"))
    assert archive.selected_sources[0].source_type == SourceType.ARCHIVE
    assert archive.required_sources == (SourceType.ARCHIVE,)

    science = route_sources(understand_question("شایع ترین کیست ادنتوژنیک چیست؟"))
    assert science.selected_sources[0].source_type == SourceType.DENTAL_KNOWLEDGE
    assert SourceType.SCIENTIFIC in science.fallback_order
    assert SourceType.ARCHIVE in science.fallback_order

    current = route_sources(understand_question("حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟"))
    assert current.selected_sources[0].source_type == SourceType.CURRENT_WEB
    assert SourceType.OFFICIAL in current.fallback_order

    hybrid = route_sources(understand_question("نظر گروه درباره کامپوزیت رو با evidence علمی مقایسه کن"))
    types = [item.source_type for item in hybrid.selected_sources]
    assert types[0] == SourceType.ARCHIVE
    assert SourceType.DENTAL_KNOWLEDGE in types and SourceType.SCIENTIFIC in types


def test_retrieval_requests_are_source_neutral_and_future_adapters_fail_explicitly():
    u = understand_question("most common odontogenic cyst?")
    route = route_sources(u)
    requests = generate_retrieval_requests(u, route)
    assert requests
    assert all(request.contract_version == "retrieval-provider-v2.0" for request in requests)
    archive_request = next(request for request in requests if request.source_type == SourceType.ARCHIVE)
    assert any(query.anchor and query.mandatory for query in archive_request.queries)
    unavailable = UnavailableRetrievalProvider(SourceType.SCIENTIFIC).retrieve(
        next(request for request in requests if request.source_type == SourceType.SCIENTIFIC)
    )
    assert unavailable.items == ()
    assert unavailable.unavailable_reason == "stage2_adapter_not_configured"


def test_intelligence_core_builds_full_decision_path_without_network_sources():
    core = DentalIntelligenceCore(registry=RetrievalRegistry())
    plan = core.plan("حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟")
    assert plan.understanding.current_information_needed
    assert plan.route.selected_sources[0].source_type == SourceType.CURRENT_WEB
    assert any(request.source_type == SourceType.CURRENT_WEB for request in plan.retrieval_requests)

from drjavanbot.intelligence.runtime import required_non_archive_sources, stage1_source_pending_answer


def test_stage1_runtime_fails_closed_when_required_external_source_is_unavailable():
    route = route_sources(understand_question("حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟"))
    assert SourceType.CURRENT_WEB in required_non_archive_sources(route)
    answer = stage1_source_pending_answer(route, ai_calls=1)
    assert answer.insufficient_evidence
    assert not answer.cited_message_ids
    assert answer.ai_calls == 1
