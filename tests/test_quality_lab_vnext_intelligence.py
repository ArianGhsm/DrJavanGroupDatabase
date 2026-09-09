from collections import Counter

from drjavanbot.intelligence.eval import vnext_cases
from drjavanbot.intelligence.routing import route_sources
from drjavanbot.intelligence.understanding import understand_question


def test_quality_lab_vnext_has_at_least_100_diverse_questions():
    cases = vnext_cases()
    assert len(cases) >= 100
    disciplines = {case.discipline for case in cases}
    assert len(disciplines) >= 15
    assert {"Career/Economics", "Regulation", "Current Market", "Oral Pathology", "Endodontics"}.issubset(disciplines)
    assert len({case.case_id for case in cases}) == len(cases)
    assert set("ABCDEFGHIJ").issubset({case.category for case in cases})
    assert sum(case.discipline == "Oral Pathology" for case in cases) >= 10
    assert sum("prevalence" in case.expected_facets for case in cases) >= 8
    assert sum(any(f in {"salary", "cost", "career", "regulation"} for f in case.expected_facets) for case in cases) >= 12
    assert all(case.gold_authority for case in cases)


def test_quality_lab_vnext_question_understanding_and_source_routing_contracts():
    failures = []
    facets_seen = Counter()
    for case in vnext_cases():
        understanding = understand_question(case.question)
        route = route_sources(understanding)
        missing = set(case.expected_facets) - set(understanding.facets)
        facets_seen.update(understanding.facets)
        if missing or route.selected_sources[0].source_type != case.expected_primary_source or understanding.archive_specific != case.expected_archive_specific:
            failures.append((case.case_id, missing, understanding.facets, route.selected_sources[0].source_type))
    assert not failures
    assert facets_seen["prevalence"] >= 5
    assert facets_seen["salary"] >= 5
    assert len(facets_seen) >= 18
