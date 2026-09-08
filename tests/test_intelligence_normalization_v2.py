from drjavanbot.normalization import normalize_text
from drjavanbot.search.terms import informative_tokens
from drjavanbot.intelligence.concepts import DentalConceptResolver


def test_persian_unicode_digits_and_zwnj_normalize_without_touching_raw_input():
    raw = "كيست‌های اودنتوژنيك ۱۲۳؟"
    normalized = normalize_text(raw)
    assert normalized == "کیست های اودنتوژنیک 123"
    assert raw == "كيست‌های اودنتوژنيك ۱۲۳؟"


def test_morphology_only_suffixes_and_copulas_do_not_become_topic_tokens():
    tokens = informative_tokens("کدام کیست‌های اودونتوژنیک رایج‌تر هستند؟")
    assert "های" not in tokens
    assert "تر" not in tokens
    assert "هستند" not in tokens
    assert "کیست" in tokens
    assert "اودونتوژنیک" in tokens


def test_odontogenic_transliteration_variants_resolve_to_one_concept_family():
    resolver = DentalConceptResolver.load_default()
    ids = []
    for variant in ("odontogenic", "اودونتوژنیک", "اودنتوژنیک", "ادنتوژنیک"):
        resolved = resolver.resolve(f"{variant} cyst")
        ids.append({item.concept_id for item in resolved})
    assert all("odontogenic" in value or "odontogenic_cyst" in value for value in ids)


def test_cyst_vs_who_is_homonym_uses_context_not_bare_string_match():
    resolver = DentalConceptResolver.load_default()
    dental = resolver.ambiguities("کدام کیست‌های اودونتوژنیک رایج‌تر هستند؟")[0]
    person = resolver.ambiguities("علی کیست؟")[0]
    assert dental.resolved_to == "cyst"
    assert person.resolved_to == "who_is"
    assert not any(item.concept_id == "cyst" for item in resolver.resolve("علی کیست؟"))
