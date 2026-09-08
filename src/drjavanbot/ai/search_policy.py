from __future__ import annotations

import re
from typing import Iterable

from drjavanbot.normalization import normalize_text
from drjavanbot.search.terms import informative_query, informative_tokens
from .query_model import EvidencePattern, RetrievalDepth, RetrievalPolicy

# These are language/intent markers only. They intentionally contain no dental
# entities, ages, doses, products, guidelines or answer facts.
_FACET_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("prevalence", ("شایع", "شایع تر", "شایع ترین", "رایج", "رایج تر", "رایج ترین", "most common", "commonest", "prevalence", "prevalent")),
    ("frequency", ("فراوانی", "frequency", "چند درصد")),
    ("epidemiology", ("اپیدمیولوژی", "epidemiology", "شیوع")),
    ("definition", ("تعریف", "چیست", "چیه", "what is", "define")),
    ("classification", ("طبقه بندی", "دسته بندی", "انواع", "classification", "types of")),
    ("diagnosis", ("تشخیص", "diagnosis", "diagnose")),
    ("differential_diagnosis", ("تشخیص افتراقی", "دیفرانسیل", "differential diagnosis", "ddx")),
    ("treatment", ("درمان", "treatment", "management")),
    ("contraindication", ("کنتراندیکاسیون", "منع مصرف", "contraindication", "contraindicated")),
    ("recurrence", ("عود", "recurrence", "recur")),
    ("follow_up", ("پیگیری", "فالوآپ", "follow up", "follow-up")),
    ("salary", ("حقوق", "درآمد", "دستمزد", "salary", "income", "compensation", "wage")),
    ("cost", ("هزینه", "قیمت", "cost", "price", "fee")),
    ("career", ("بازار کار", "تازه فارغ", "فارغ التحصیل", "career", "job", "new graduate")),
    ("regulation", ("قانون", "مقررات", "آیین نامه", "مجوز", "regulation", "law", "license")),
    ("timing_age", (" سن ", "چه سنی", "چند سالگی", "از چه سن", "در چه سن", "سن مناسب", "سن شروع", "age", "what age", "how old")),
    ("timing", ("چه زمانی", "چه موقع", "کی باید", "زمان شروع", "زمان مناسب", "when", "timing")),
    ("pediatric_population", ("بچه", "بچه ها", "کودک", "کودکان", "اطفال", "نوجوان", "child", "children", "pediatric", "adolescent")),
    ("comparison", ("مقایسه", "کدوم بهتر", "کدام بهتر", " یا ", " vs ", "versus", "compare", "which is better")),
    ("recommendation", ("پیشنهاد", "توصیه", "خوبه", "خوب است", "بهترین", "recommend", "recommended", "best")),
    ("cause_reason", ("چرا", "علت", "دلیل", "به چه علت", "cause", "reason", "why")),
    ("method_how", ("چطور", "چگونه", "روش", "مراحل", "نحوه", "how", "technique", "steps")),
    ("dosage", ("دوز", "دوزاژ", "dose", "dosage")),
    ("quantity", ("چقدر", "چند تا", "چه مقدار", "مقدار", "how much", "how many", "quantity")),
    ("indication", ("اندیکاسیون", "موارد استفاده", "چه موردی", "چه مواقعی", "indication", "when indicated")),
    ("complication", ("عارضه", "عوارض", "مشکل بعد", "complication", "adverse")),
    ("prognosis", ("پیش اگهی", "پیش آگهی", "پروگنوز", "prognosis", "outcome")),
)

_FACET_QUERY_TERMS: dict[str, tuple[str, ...]] = {
    "prevalence": ("شایع", "رایج", "شایع ترین", "most common", "prevalence"),
    "frequency": ("فراوانی", "frequency", "درصد"),
    "epidemiology": ("شیوع", "اپیدمیولوژی", "epidemiology", "prevalence"),
    "definition": ("تعریف", "یعنی", "definition"),
    "classification": ("انواع", "طبقه بندی", "classification"),
    "diagnosis": ("تشخیص", "diagnosis"),
    "differential_diagnosis": ("تشخیص افتراقی", "differential diagnosis", "ddx"),
    "treatment": ("درمان", "treatment", "management"),
    "contraindication": ("کنتراندیکاسیون", "منع مصرف", "contraindication"),
    "recurrence": ("عود", "recurrence"),
    "follow_up": ("پیگیری", "follow up"),
    "salary": ("حقوق", "درآمد", "دستمزد", "salary", "income"),
    "cost": ("هزینه", "قیمت", "cost", "price"),
    "career": ("بازار کار", "فارغ التحصیل", "career", "job"),
    "regulation": ("قانون", "مقررات", "مجوز", "regulation", "law"),
    "timing_age": ("سن", "سالگی", "سن شروع", "زمان", "age", "timing"),
    "timing": ("زمان", "زمان شروع", "چه زمانی", "timing"),
    "pediatric_population": ("کودک", "بچه", "نوجوان", "pediatric"),
    "comparison": ("مقایسه", "بهتر", "versus", "compare"),
    "recommendation": ("پیشنهاد", "توصیه", "بهترین", "recommend"),
    "cause_reason": ("علت", "دلیل", "cause", "reason"),
    "method_how": ("روش", "نحوه", "مراحل", "technique"),
    "dosage": ("دوز", "دوزاژ", "dose", "dosage"),
    "quantity": ("مقدار", "تعداد", "quantity", "amount"),
    "indication": ("اندیکاسیون", "موارد استفاده", "indication"),
    "complication": ("عارضه", "عوارض", "complication"),
    "prognosis": ("پیش آگهی", "پروگنوز", "prognosis"),
}

_DEEP_FACETS = frozenset({
    "timing_age", "comparison", "recommendation", "cause_reason", "method_how",
    "quantity", "dosage", "indication", "complication", "prognosis",
    "prevalence", "frequency", "epidemiology", "diagnosis", "differential_diagnosis", "treatment",
    "contraindication", "recurrence", "follow_up", "salary", "cost", "career", "regulation",
})
_MULTI_SOURCE_FACETS = frozenset({"comparison", "recommendation", "prevalence", "frequency", "epidemiology", "salary", "cost"})
_FRAGMENTED_FACETS = frozenset({"timing_age", "timing", "cause_reason", "method_how", "quantity", "dosage"})
_DIGIT_RE = re.compile(r"\d")


def infer_question_facets(question: str) -> tuple[str, ...]:
    """Infer answer dimensions from generic language, never domain answer facts."""
    normalized = normalize_text(question)
    if not normalized:
        return ()
    padded = f" {normalized} "
    out: list[str] = []
    for facet, markers in _FACET_MARKERS:
        if any(_marker_matches(padded, marker) for marker in markers):
            out.append(facet)
    if "timing_age" in out and "timing" in out:
        out.remove("timing")
    return tuple(out)


def facet_query_terms(facet: str) -> tuple[str, ...]:
    return _FACET_QUERY_TERMS.get(str(facet), ())


def comparison_targets(question: str) -> tuple[str, ...]:
    """Extract user-provided comparison sides on token boundaries only."""
    normalized = normalize_text(question)
    for separator in ("یا", "vs", "versus"):
        parts = re.split(rf"\s+{re.escape(separator)}\s+", normalized, maxsplit=3)
        if len(parts) < 2:
            continue
        values = tuple(value for value in (informative_query(part) for part in parts) if value)
        if len(values) >= 2:
            return values[:4]
    return ()


def derive_retrieval_policy(question: str, *, facets: Iterable[str], family_count: int) -> RetrievalPolicy:
    facet_set = set(facets)
    informative = informative_tokens(question)
    faceted = bool(facet_set & _DEEP_FACETS)
    direct = not faceted and len(informative) <= 3

    if direct:
        return RetrievalPolicy(
            depth=RetrievalDepth.DIRECT,
            query_budget=4,
            family_budget=3,
            per_family_budget=2,
            rescue_allowed=True,
            max_rescue_families=2,
            expected_evidence_pattern=EvidencePattern.SINGLE_MESSAGE,
            stop_when_required_facets_covered=True,
            minimum_family_coverage=1,
        )

    if facet_set & _MULTI_SOURCE_FACETS:
        pattern = EvidencePattern.MULTI_SOURCE
    elif facet_set & _FRAGMENTED_FACETS or "pediatric_population" in facet_set:
        pattern = EvidencePattern.FRAGMENTED_DISCUSSION
    else:
        pattern = EvidencePattern.REPLY_CONTEXT

    if faceted:
        query_budget = min(20, max(12, 4 + 3 * len(facet_set)))
        family_budget = min(10, max(5, family_count, len(facet_set) + 2))
        coverage = min(family_budget, max(2, min(4, len(facet_set) + 1)))
        return RetrievalPolicy(
            depth=RetrievalDepth.DEEP,
            query_budget=query_budget,
            family_budget=family_budget,
            per_family_budget=3,
            rescue_allowed=True,
            max_rescue_families=4,
            expected_evidence_pattern=pattern,
            stop_when_required_facets_covered=True,
            minimum_family_coverage=coverage,
        )

    return RetrievalPolicy(
        depth=RetrievalDepth.STANDARD,
        query_budget=10,
        family_budget=min(6, max(3, family_count)),
        per_family_budget=3,
        rescue_allowed=True,
        max_rescue_families=3,
        expected_evidence_pattern=pattern,
        stop_when_required_facets_covered=True,
        minimum_family_coverage=2 if family_count > 1 else 1,
    )


def has_novel_numeric_hint(value: str, *, question: str) -> bool:
    """Reject model-invented numeric search hints; user-supplied numerics may be searched."""
    normalized = normalize_text(value)
    if not _DIGIT_RE.search(normalized):
        return False
    question_normalized = normalize_text(question)
    value_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", normalized))
    question_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", question_normalized))
    return bool(value_numbers - question_numbers)


def sanitize_hint(value: str, *, question: str, max_len: int = 120) -> str:
    text = " ".join(str(value).strip().split())[:max_len]
    if not text or has_novel_numeric_hint(text, question=question):
        return ""
    return text


def _marker_matches(padded_normalized: str, marker: str) -> bool:
    needle = normalize_text(marker)
    if not needle:
        return False
    if marker.startswith(" ") or marker.endswith(" "):
        return f" {needle} " in padded_normalized
    return needle in padded_normalized


__all__ = [
    "infer_question_facets", "facet_query_terms", "comparison_targets",
    "derive_retrieval_policy", "has_novel_numeric_hint", "sanitize_hint",
]
