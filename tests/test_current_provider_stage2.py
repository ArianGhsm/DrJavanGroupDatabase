from drjavanbot.intelligence.current_provider import CurrentInformationProvider, _admit_current_evidence
from drjavanbot.intelligence.models import EvidenceItem, Geography, RetrievalQuery, RetrievalRequest, SourceType


def _request():
    return RetrievalRequest(source_type=SourceType.CURRENT_WEB, normalized_question="حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره", queries=(RetrievalQuery("درآمد دندانپزشک ایران 1405", "current_salary"),), entity_ids=("dentistry","new_graduate"), facets=("salary","career"), freshness="current", geography=Geography(country_code="IR",label="Iran",explicit=True,source="question"), top_k=5)


def _item(text, *, domain="dentkar.com", current=True, numeric=True, trust=.82):
    return EvidenceItem(evidence_id="e",source_type=SourceType.CURRENT_WEB,source_name=domain,source_ref=f"https://{domain}/x",text=text,title="درآمد دندانپزشک در ایران ۱۴۰۵",timestamp="2026-08-01T00:00:00+00:00",metadata={"current_year_signal":current,"geography_signal":True,"numeric_signal":numeric,"monetary_signal":numeric},trust_tier="current_web",trust_score=trust,url=f"https://{domain}/x")


def test_salary_current_admission_requires_dental_compensation_numeric_and_current():
    req=_request()
    assert _admit_current_evidence(_item("دندانپزشک تازه کار ۴۰ تا ۹۰ میلیون تومان درآمد دارد"),req)
    assert not _admit_current_evidence(_item("حقوق کارگران ۴۰ میلیون تومان",domain="dentkar.com"),req)
    assert not _admit_current_evidence(_item("دندانپزشک تازه کار و وضعیت بازار",numeric=False),req)
    assert not _admit_current_evidence(_item("دندانپزشک ۴۰ میلیون تومان",trust=.58),req)

def test_official_provider_does_not_relabel_current_seed_as_official():
    from drjavanbot.intelligence.current_provider import OfficialInformationProvider, _trusted_seed_urls
    from dataclasses import replace
    req=_request()
    official_req=replace(req, source_type=SourceType.OFFICIAL)
    provider=OfficialInformationProvider(max_queries=1,max_page_fetches=0)
    for url in _trusted_seed_urls(official_req):
        assert not provider._admit_url(url, official_req)


def test_monetary_signal_rejects_year_only_and_extracts_currency_range():
    from drjavanbot.intelligence.current_provider import _has_monetary_signal, _extract_monetary_windows
    assert not _has_monetary_signal("درآمد دندانپزشک در ۱۴۰۵ به روز شد و تورم ۸۰ درصد بود")
    text="برای تازه کارها درآمد ماهانه حدود ۴۰ تا ۹۰ میلیون تومان است و بسته به قرارداد فرق می کند."
    assert _has_monetary_signal(text)
    window=_extract_monetary_windows("مقدمه "*300 + text + " پایان"*300)
    assert "۴۰ تا ۹۰ میلیون تومان" in window
