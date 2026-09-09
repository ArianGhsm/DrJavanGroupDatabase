from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from drjavanbot.normalization import normalize_text
from .models import FreshnessClass, SourceType


@dataclass(frozen=True, slots=True)
class FacetSpec:
    name: str
    markers: tuple[str, ...]
    evidence_markers: tuple[str, ...]
    evidence_shape: str
    freshness_sensitivity: str
    source_affinity: tuple[str, ...]
    requires_numeric: bool = False
    requires_current_timestamp: bool = False


_SCIENCE = (SourceType.DENTAL_KNOWLEDGE, SourceType.SCIENTIFIC, SourceType.ARCHIVE)
_CURRENT = (SourceType.CURRENT_WEB, SourceType.OFFICIAL, SourceType.ARCHIVE)
_ARCHIVE_FRIENDLY = (SourceType.ARCHIVE, SourceType.DENTAL_KNOWLEDGE, SourceType.SCIENTIFIC)


def _s(
    name: str,
    markers: Iterable[str],
    evidence_markers: Iterable[str] = (),
    *,
    shape: str = "textual",
    freshness: str = FreshnessClass.EVERGREEN,
    affinity: tuple[str, ...] = _SCIENCE,
    numeric: bool = False,
    current_timestamp: bool = False,
) -> FacetSpec:
    return FacetSpec(
        name=name,
        markers=tuple(markers),
        evidence_markers=tuple(evidence_markers or markers),
        evidence_shape=shape,
        freshness_sensitivity=freshness,
        source_affinity=tuple(str(x) for x in affinity),
        requires_numeric=numeric,
        requires_current_timestamp=current_timestamp,
    )


FACET_SPECS: tuple[FacetSpec, ...] = (
    _s("definition", ("تعریف", "چیست", "چیه", "what is", "define"), ("تعریف", "یعنی", "عبارت است", "refers to", "defined as")),
    _s("classification", ("طبقه بندی", "دسته بندی", "انواع", "classification", "types of"), ("طبقه بندی", "دسته", "نوع", "classification", "type")),
    _s("prevalence", ("شایع", "شایع تر", "شایع ترین", "رایج", "رایج تر", "رایج ترین", "بیشتر دیده", "بیشتر مشاهده", "most common", "more common", "seen more often", "commonest", "prevalent", "prevalence"), ("شایع", "رایج", "فراوان", "most common", "commonest", "prevalent", "prevalence", "frequency"), shape="frequency_comparison"),
    _s("frequency", ("فراوانی", "frequency", "چند درصد", "چقدر دیده", "چقدر رخ"), ("فراوانی", "frequency", "درصد", "percent", "%", "نرخ"), shape="frequency", numeric=False),
    _s("epidemiology", ("اپیدمیولوژی", "epidemiology", "شیوع"), ("اپیدمیولوژی", "epidemiology", "شیوع", "prevalence", "incidence"), shape="epidemiologic"),
    _s("age", ("چه سن", "چند سالگی", "سن", "age", "how old"), ("سن", "سالگی", "age", "year"), shape="numeric_or_age", numeric=True),
    _s("sex", ("جنس", "مرد", "زن", "male", "female", "sex", "gender"), ("مرد", "زن", "male", "female", "sex", "gender"), shape="distribution"),
    _s("population", ("جمعیت", "کودک", "بچه", "نوجوان", "بزرگسال", "population", "children", "pediatric", "adult", "adults"), ("کودک", "بچه", "نوجوان", "بزرگسال", "population", "children", "pediatric", "adult", "adults"), shape="population"),
    _s("location", ("کجا", "محل", "ناحیه", "location", "site", "where"), ("محل", "ناحیه", "location", "site", "فک", "قدام", "خلف"), shape="anatomic_location"),
    _s("distribution", ("توزیع", "distribution", "پراکندگی"), ("توزیع", "distribution", "پراکندگی"), shape="distribution"),
    _s("etiology", ("اتیولوژی", "etiology"), ("اتیولوژی", "etiology", "ناشی", "مرتبط"), shape="causal"),
    _s("cause", ("علت", "چرا", "دلیل", "cause", "reason", "why"), ("علت", "دلیل", "ناشی", "cause", "reason", "because", "due to"), shape="causal"),
    _s("risk_factor", ("ریسک فاکتور", "عامل خطر", "عوامل خطر", "risk factor"), ("ریسک", "عامل خطر", "risk factor", "associated with"), shape="risk_relation"),
    _s("signs", ("علامت", "علائم", "sign", "signs"), ("علامت", "علائم", "sign", "signs"), shape="clinical_features"),
    _s("symptoms", ("سمپتوم", "نشانه", "علائم", "symptom", "symptoms"), ("نشانه", "علائم", "symptom", "symptoms", "درد", "تورم"), shape="clinical_features"),
    _s("diagnosis", ("تشخیص", "تشخیصش", "diagnosis", "diagnose"), ("تشخیص", "diagnosis", "diagnostic"), shape="diagnostic"),
    _s("differential_diagnosis", ("تشخیص افتراقی", "دیفرانسیل", "differential diagnosis", "ddx"), ("تشخیص افتراقی", "دیفرانسیل", "differential", "ddx"), shape="differential"),
    _s("radiographic_features", ("رادیوگراف", "رادیوگرافی", "رادیولوژ", "رادیولوژی", "radiographic", "radiological", "x ray", "xray"), ("رادیوگراف", "radiographic", "radiolucent", "radiopaque", "x ray"), shape="imaging_features"),
    _s("histopathology", ("هیستوپات", "بافت شناسی", "histopathology", "histology"), ("هیستو", "بافت", "histopathology", "histology", "microscopic"), shape="pathology_features"),
    _s("indication", ("اندیکاسیون", "موارد استفاده", "چه مواقعی", "indication", "indicated"), ("اندیکاسیون", "موارد استفاده", "indication", "indicated"), shape="indication"),
    _s("contraindication", ("کنتراندیکاسیون", "منع مصرف", "ممنوع", "contraindication", "contraindicated"), ("کنتراندیکاسیون", "منع", "contraindication", "contraindicated"), shape="contraindication"),
    _s("treatment", ("درمان", "درمانش", "درمانی", "treatment", "manage", "management"), ("درمان", "treatment", "management", "درمان می"), shape="management"),
    _s("technique", ("تکنیک", "technique", "روش انجام"), ("تکنیک", "technique", "روش", "مرحله"), shape="procedural"),
    _s("method", ("روش", "چطور", "چگونه", "نحوه", "method", "how", "steps"), ("روش", "نحوه", "مرحله", "method", "step", "technique"), shape="procedural"),
    _s("dosage", ("دوز", "دوزاژ", "dose", "dosage"), ("دوز", "dose", "dosage", "mg", "میلی گرم", "mcg", "ml"), shape="numeric_dose", numeric=True),
    _s("timing", ("چه زمانی", "چه موقع", "زمان", "when", "timing"), ("زمان", "قبل", "بعد", "when", "timing", "before", "after"), shape="temporal"),
    _s("duration", ("مدت", "چقدر طول", "duration", "how long"), ("مدت", "روز", "هفته", "ماه", "duration", "day", "week", "month"), shape="duration", numeric=True),
    _s("comparison", ("مقایسه", "در مقایسه", "vs", "versus", "کدوم بهتر", "کدام بهتر"), ("بهتر", "کمتر", "بیشتر", "مقایسه", "versus", "vs", "compared"), shape="comparative"),
    _s("recommendation", ("پیشنهاد", "توصیه", "بهترین", "خوبه", "خوب است", "recommend", "recommended", "best", "is it good"), ("پیشنهاد", "توصیه", "بهترین", "recommend", "recommended", "prefer"), shape="recommendation", affinity=_ARCHIVE_FRIENDLY),
    _s("prognosis", ("پیش آگهی", "پروگنوز", "prognosis", "outcome"), ("پیش آگهی", "پروگنوز", "prognosis", "outcome", "موفقیت"), shape="outcome"),
    _s("recurrence", ("عود", "عودش", "recurrence", "recur"), ("عود", "recurrence", "recur", "عود می"), shape="outcome"),
    _s("complication", ("عارضه", "عوارض", "complication", "adverse"), ("عارضه", "عوارض", "complication", "adverse"), shape="adverse_outcome"),
    _s("follow_up", ("پیگیری", "فالوآپ", "follow up", "follow-up"), ("پیگیری", "فالوآپ", "follow up", "follow-up", "کنترل"), shape="follow_up"),
    _s("material", ("ماده", "متریال", "material"), ("ماده", "متریال", "material"), shape="entity_fact", affinity=_ARCHIVE_FRIENDLY),
    _s("product", ("محصول", "برند", "مارک", "product", "brand"), ("محصول", "برند", "مارک", "product", "brand"), shape="entity_fact", affinity=_ARCHIVE_FRIENDLY),
    _s("cost", ("هزینه", "قیمت", "fee", "cost", "price"), ("هزینه", "قیمت", "تومان", "ریال", "fee", "cost", "price"), shape="numeric_current", freshness=FreshnessClass.CURRENT, affinity=_CURRENT, numeric=True, current_timestamp=True),
    _s("salary", ("حقوق", "درآمد", "دستمزد", "salary", "income", "compensation", "wage"), ("حقوق", "درآمد", "دستمزد", "salary", "income", "compensation", "wage", "تومان", "ریال"), shape="numeric_current", freshness=FreshnessClass.CURRENT, affinity=_CURRENT, numeric=True, current_timestamp=True),
    _s("career", ("شغل", "بازار کار", "تازه فارغ", "فارغ التحصیل", "تازه کار", "تازه‌کار", "career", "job", "new graduate"), ("شغل", "بازار کار", "فارغ", "تازه کار", "career", "job", "graduate"), shape="current_context", freshness=FreshnessClass.CURRENT, affinity=_CURRENT),
    _s("regulation", ("قانون", "مقررات", "آیین نامه", "مجوز", "regulation", "law", "license"), ("قانون", "مقررات", "آیین نامه", "مجوز", "regulation", "law", "license"), shape="official_current", freshness=FreshnessClass.CURRENT, affinity=(SourceType.OFFICIAL, SourceType.CURRENT_WEB, SourceType.ARCHIVE), current_timestamp=True),
    _s("guideline", ("گایدلاین", "راهنمای بالینی", "guideline", "recommendation guideline"), ("گایدلاین", "guideline", "recommendation", "consensus"), shape="guideline", freshness=FreshnessClass.RECENT, affinity=(SourceType.SCIENTIFIC, SourceType.OFFICIAL, SourceType.DENTAL_KNOWLEDGE)),
    _s("clinical_decision", ("تصمیم درمانی", "چه کار کنم", "انتخاب درمان", "clinical decision", "treatment planning"), ("تصمیم", "انتخاب", "درمان", "decision", "plan"), shape="decision", affinity=(SourceType.DENTAL_KNOWLEDGE, SourceType.SCIENTIFIC, SourceType.OFFICIAL)),
)

FACET_BY_NAME = {spec.name: spec for spec in FACET_SPECS}


def facet_spec(name: str) -> FacetSpec | None:
    return FACET_BY_NAME.get(str(name))


def detect_facets(question: str) -> tuple[str, ...]:
    normalized = normalize_text(question)
    if not normalized:
        return ()
    padded = f" {normalized} "
    found: list[str] = []
    for spec in FACET_SPECS:
        if any(_marker_match(padded, marker) for marker in spec.markers):
            found.append(spec.name)
    # Guard common non-dental homonyms before copular-definition suppression.
    if any(value in normalized for value in ("حقوق بشر", "حقوق مدنی", "human rights", "legal rights")) and "salary" in found:
        found.remove("salary")
    if any(value in normalized for value in ("cost function", "تابع هزینه", "loss function")) and "cost" in found:
        found.remove("cost")

    # More specific facets subsume their broad linguistic neighbours.
    if "differential_diagnosis" in found and "diagnosis" in found:
        found.remove("diagnosis")
    if "dosage" in found and "method" in found and not _explicit_method(normalized):
        found.remove("method")
    if "prevalence" in found and "recommendation" in found:
        # "رایج/شایع" is a frequency request, not a recommendation.
        found.remove("recommendation")
    if "population" in found and any(value in normalized for value in ("بچه های گروه", "بچه های گروه", "بچه‌های گروه")):
        if not any(value in normalized for value in ("کودک", "نوجوان", "pediatric", "child", "adult")):
            found.remove("population")
    if "treatment" in found and any(value in found for value in ("contraindication", "prognosis", "follow_up", "duration", "cost")):
        if not _explicit_treatment_request(normalized):
            found.remove("treatment")
    if "technique" in found and "method" in found and not any(value in normalized for value in ("تکنیک", "technique")):
        found.remove("technique")
    if "definition" in found and len(found) > 1:
        # Persian copular frames such as "X چیه؟" often terminate a more
        # specific request (diagnosis, prevalence, guideline, etc.). Keep
        # definition only when it is explicit or is the sole requested facet.
        explicit_definition = any(value in normalized for value in ("تعریف", "define", "definition"))
        if not explicit_definition:
            found.remove("definition")
    return tuple(dict.fromkeys(found))


def evidence_signal(text: str, facet: str) -> tuple[bool, float, str]:
    spec = facet_spec(facet)
    normalized = normalize_text(text)
    if spec is None:
        return False, 0.0, "unknown_facet"
    marker_hit = any(_marker_match(f" {normalized} ", marker) for marker in spec.evidence_markers)
    numeric_hit = bool(re.search(r"(?<!\w)\d+(?:[.,]\d+)?(?:\s*%|\s*درصد)?(?!\w)", normalized))
    if spec.requires_numeric and not numeric_hit:
        return False, 0.0, "numeric_signal_missing"
    if not marker_hit:
        return False, 0.0, "facet_semantic_signal_missing"
    if spec.requires_numeric:
        return True, 1.0, "facet_and_numeric_signal"
    return True, 0.9, "facet_semantic_signal"


def _marker_match(padded: str, marker: str) -> bool:
    needle = normalize_text(marker)
    if not needle:
        return False
    if " " in needle:
        return needle in padded
    if re.search(r"[\u0600-\u06ff]", needle):
        # Persian colloquial writing frequently attaches clitic/plural suffixes
        # (e.g. درآمدش، شیوعش، تازه‌کارها). Keep the allowed suffix set small.
        return re.search(rf"(?<![\w\u0600-\u06ff]){re.escape(needle)}(?:ش|م|ت|ها|های)?(?![\w\u0600-\u06ff])", padded) is not None
    return re.search(rf"(?<![\w\u0600-\u06ff]){re.escape(needle)}(?![\w\u0600-\u06ff])", padded) is not None


def _explicit_method(normalized: str) -> bool:
    return any(value in normalized for value in ("روش", "چطور", "چگونه", "نحوه", "method", "how", "steps"))


def _explicit_treatment_request(normalized: str) -> bool:
    return any(value in normalized for value in (
        "درمانش", "درمان چیست", "درمان چیه", "درمان چی", "چه درمان", "و درمان",
        "درمانی پیشنهاد", "treatment", "management", "manage",
    ))


__all__ = ["FacetSpec", "FACET_SPECS", "FACET_BY_NAME", "detect_facets", "evidence_signal", "facet_spec"]
