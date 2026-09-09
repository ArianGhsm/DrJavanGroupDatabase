from drjavanbot.intelligence.models import Geography, RetrievalQuery, RetrievalRequest, SourceType
from drjavanbot.intelligence.scientific_provider import PubMedScientificProvider


def _request():
    return RetrievalRequest(source_type=SourceType.SCIENTIFIC, normalized_question="most common odontogenic cyst", queries=(RetrievalQuery("odontogenic cyst prevalence", "scientific_prevalence"),), entity_ids=("odontogenic_cyst",), facets=("prevalence",), freshness="evergreen", geography=Geography(), top_k=5)


def test_pubmed_provider_maps_authoritative_metadata_without_full_text(monkeypatch):
    provider=PubMedScientificProvider()
    monkeypatch.setattr(provider,"_search",lambda term,retmax:("23766099",))
    monkeypatch.setattr(provider,"_fetch",lambda ids:({"pmid":"23766099","title":"Global epidemiology of odontogenic cysts","abstract":"Radicular cyst was the most common odontogenic cyst, accounting for 54.6%.","authors":("A Researcher",),"journal":"Journal X","year":2014,"publication_types":("Systematic Review",),"doi":"10.1/test"},))
    result=provider.retrieve(_request())
    assert result.unavailable_reason is None and len(result.items)==1
    item=result.items[0]
    assert item.evidence_id=="pubmed:23766099"
    assert item.metadata["pmid"]=="23766099" and item.metadata["doi"]=="10.1/test"
    assert item.publication_year==2014 and item.trust_tier=="systematic_review"
    assert item.trust_score>=0.95 and item.url.endswith("/23766099/")
    assert "Radicular" in item.text
