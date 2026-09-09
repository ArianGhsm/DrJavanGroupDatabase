from __future__ import annotations

from dataclasses import dataclass, replace

from .models import SourceType


@dataclass(frozen=True, slots=True)
class IntelligenceEvalCase:
    case_id: str
    discipline: str
    question: str
    expected_facets: tuple[str, ...]
    expected_primary_source: str
    expected_archive_specific: bool = False
    category: str = ""
    gold_authority: str = ""
    gold_refs: tuple[str, ...] = ()


def vnext_cases() -> tuple[IntelligenceEvalCase, ...]:
    c = IntelligenceEvalCase
    cases = (
        c("op01", "Oral Pathology", "کدام کیست‌های اودونتوژنیک رایج‌تر هستند؟", ("prevalence",), SourceType.SCIENTIFIC),
        c("op02", "Oral Pathology", "شایع‌ترین کیست ادنتوژنیک چیست؟", ("prevalence",), SourceType.SCIENTIFIC),
        c("op03", "Oral Pathology", "most common odontogenic cyst?", ("prevalence",), SourceType.SCIENTIFIC),
        c("op04", "Oral Pathology", "کدوم سیست اودنتوژنیک بیشتر دیده میشه؟", ("prevalence",), SourceType.SCIENTIFIC),
        c("op05", "Oral Pathology", "طبقه بندی کیست های فکی چیه؟", ("classification",), SourceType.SCIENTIFIC),
        c("op06", "Oral Pathology", "عود این ضایعه چقدره؟", ("recurrence",), SourceType.SCIENTIFIC),
        c("om01", "Oral Medicine", "تشخیص افتراقی این ضایعه دهانی چیه؟", ("differential_diagnosis",), SourceType.SCIENTIFIC),
        c("om02", "Oral Medicine", "علائم این بیماری دهان چی هستند؟", ("signs", "symptoms"), SourceType.SCIENTIFIC),
        c("om03", "Oral Medicine", "risk factor های این مشکل دهانی چیان؟", ("risk_factor",), SourceType.SCIENTIFIC),
        c("om04", "Oral Medicine", "برای این وضعیت چه درمانی پیشنهاد میشه؟", ("treatment", "recommendation"), SourceType.SCIENTIFIC),
        c("rad01", "Oral Radiology", "radiographic features این ضایعه چیه؟", ("radiographic_features",), SourceType.SCIENTIFIC),
        c("rad02", "Oral Radiology", "در رادیوگرافی تشخیصش چیه؟", ("radiographic_features", "diagnosis"), SourceType.SCIENTIFIC),
        c("rad03", "Oral Radiology", "این نمای x-ray با اون یکی چه مقایسه‌ای داره؟", ("radiographic_features", "comparison"), SourceType.SCIENTIFIC),
        c("rad04", "Oral Radiology", "محل شایع این یافته radiographic کجاست؟", ("location", "prevalence", "radiographic_features"), SourceType.SCIENTIFIC),
        c("endo01", "Endodontics", "diagnosis پالپیت چطور انجام میشه؟", ("diagnosis", "method"), SourceType.SCIENTIFIC),
        c("endo02", "Endodontics", "تکنیک working length رو توضیح بده", ("technique",), SourceType.SCIENTIFIC),
        c("endo03", "Endodontics", "عوارض RCT چی میتونه باشه؟", ("complication",), SourceType.SCIENTIFIC),
        c("endo04", "Endodontics", "prognosis درمان ریشه چطوره؟", ("prognosis",), SourceType.SCIENTIFIC),
        c("perio01", "Periodontics", "تعریف periodontitis چیه؟", ("definition",), SourceType.SCIENTIFIC),
        c("perio02", "Periodontics", "اندیکاسیون جراحی پریو چیه؟", ("indication",), SourceType.SCIENTIFIC),
        c("perio03", "Periodontics", "contraindication این درمان پریو چیه؟", ("contraindication",), SourceType.SCIENTIFIC),
        c("perio04", "Periodontics", "فالوآپ بعد درمان لثه چه زمانی باشه؟", ("follow_up", "timing"), SourceType.SCIENTIFIC),
        c("rest01", "Restorative", "کامپوزیت یا آمالگام کدوم بهتره؟", ("comparison",), SourceType.SCIENTIFIC),
        c("rest02", "Restorative", "روش bonding رو چطور انجام میدن؟", ("method",), SourceType.SCIENTIFIC),
        c("rest03", "Restorative", "اندیکاسیون گلاس آینومر چیه؟", ("indication",), SourceType.SCIENTIFIC),
        c("rest04", "Restorative", "چه material ای برای این ترمیم مناسبه؟", ("material",), SourceType.SCIENTIFIC),
        c("pros01", "Prosthodontics", "e.max و زیرکونیا رو مقایسه کن", ("comparison",), SourceType.SCIENTIFIC),
        c("pros02", "Prosthodontics", "تکنیک قالبگیری پروتز چیه؟", ("technique",), SourceType.SCIENTIFIC),
        c("pros03", "Prosthodontics", "عوارض روکش چی هست؟", ("complication",), SourceType.SCIENTIFIC),
        c("pros04", "Prosthodontics", "گروه درباره e.max چی گفته؟", (), SourceType.ARCHIVE, True),
        c("ortho01", "Orthodontics", "برای بچه ها ارتودنسی از چه سنی؟", ("age", "population"), SourceType.SCIENTIFIC),
        c("ortho02", "Orthodontics", "indication ارتودنسی چیه؟", ("indication",), SourceType.SCIENTIFIC),
        c("ortho03", "Orthodontics", "fixed vs removable orthodontics رو مقایسه کن", ("comparison",), SourceType.SCIENTIFIC),
        c("ortho04", "Orthodontics", "duration درمان ارتودنسی چقدره؟", ("duration",), SourceType.SCIENTIFIC),
        c("pedo01", "Pediatric Dentistry", "دوز دارو برای کودک چقدره؟", ("dosage", "population"), SourceType.SCIENTIFIC),
        c("pedo02", "Pediatric Dentistry", "زمان درمان پالپ در بچه ها کیه؟", ("timing", "population", "treatment"), SourceType.SCIENTIFIC),
        c("pedo03", "Pediatric Dentistry", "contraindication پالپوتومی در کودک؟", ("contraindication", "population"), SourceType.SCIENTIFIC),
        c("pedo04", "Pediatric Dentistry", "prognosis درمان دندان شیری؟", ("prognosis",), SourceType.SCIENTIFIC),
        c("surg01", "Oral Surgery", "اندیکاسیون کشیدن دندان عقل چیه؟", ("indication",), SourceType.SCIENTIFIC),
        c("surg02", "Oral Surgery", "عوارض بعد جراحی دهان چیان؟", ("complication",), SourceType.SCIENTIFIC),
        c("surg03", "Oral Surgery", "چه زمانی باید follow-up بشه؟", ("timing", "follow_up"), SourceType.SCIENTIFIC),
        c("surg04", "Oral Surgery", "روش انجام این جراحی چیه؟", ("method",), SourceType.SCIENTIFIC),
        c("impl01", "Implant Dentistry", "contraindication ایمپلنت چیه؟", ("contraindication",), SourceType.SCIENTIFIC),
        c("impl02", "Implant Dentistry", "success/prognosis ایمپلنت چطوره؟", ("prognosis",), SourceType.SCIENTIFIC),
        c("impl03", "Implant Dentistry", "قیمت ایمپلنت الان در ایران چقدره؟", ("cost",), SourceType.CURRENT_WEB),
        c("impl04", "Implant Dentistry", "گروه درباره برند ایمپلنت چی پیشنهاد داده؟", ("product", "recommendation"), SourceType.ARCHIVE, True),
        c("pharm01", "Pharmacology", "دوز آموکسی سیلین در دندانپزشکی چقدره؟", ("dosage",), SourceType.SCIENTIFIC),
        c("pharm02", "Pharmacology", "contraindication این دارو چیه؟", ("contraindication",), SourceType.SCIENTIFIC),
        c("pharm03", "Pharmacology", "عوارض این antibiotic چیه؟", ("complication",), SourceType.SCIENTIFIC),
        c("pharm04", "Pharmacology", "guideline جدید آنتی بیوتیک پروفیلاکسی چیه؟", ("guideline",), SourceType.SCIENTIFIC),
        c("career01", "Career/Economics", "حقوق دانشجوهای تازه فارغ التحصیل شده چقدره؟", ("salary", "career"), SourceType.CURRENT_WEB),
        c("career02", "Career/Economics", "حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟", ("salary", "career"), SourceType.CURRENT_WEB),
        c("career03", "Career/Economics", "salary new graduate dentist in Iran?", ("salary", "career"), SourceType.CURRENT_WEB),
        c("career04", "Career/Economics", "بازار کار دندانپزشکی الان چطوره؟", ("career",), SourceType.CURRENT_WEB),
        c("career05", "Career/Economics", "گروه درباره حقوق دندانپزشکا چی گفته؟", ("salary",), SourceType.ARCHIVE, True),
        c("career06", "Career/Economics", "درباره حقوق دندانپزشک تازه‌کار هم نظر گروه رو بگو هم وضعیت الان ایران رو", ("salary", "career"), SourceType.ARCHIVE, True),
        c("reg01", "Regulation", "قانون جدید مجوز مطب دندانپزشکی چیه؟", ("regulation",), SourceType.OFFICIAL),
        c("reg02", "Regulation", "مقررات فعلی تبلیغات دندانپزشکی در ایران؟", ("regulation",), SourceType.OFFICIAL),
        c("reg03", "Regulation", "official regulation for dental clinic license in Iran", ("regulation",), SourceType.OFFICIAL),
        c("current01", "Current Market", "قیمت کامپوزیت امروز تو ایران چنده؟", ("cost",), SourceType.CURRENT_WEB),
        c("current02", "Current Market", "latest price e.max ایران", ("cost",), SourceType.CURRENT_WEB),
        c("hybrid01", "Hybrid", "بچه های گروه درباره e.max چی گفتن و مقالات چی میگن؟", (), SourceType.ARCHIVE, True),
        c("hybrid02", "Hybrid", "نظر گروه درباره کامپوزیت رو با evidence علمی مقایسه کن", ("comparison",), SourceType.ARCHIVE, True),
        c("adv01", "Adversarial", "علی کیست؟", (), SourceType.NONE),
        c("adv02", "Adversarial", "odontogenic cyst prevalence؟", ("prevalence",), SourceType.SCIENTIFIC),

        # Stage 2 final lab: A — Archive-only
        c("a01", "Archive-only", "گروه درباره RCT چی گفته؟", (), SourceType.ARCHIVE, True, "A"),
        c("a02", "Archive-only", "تو گروه درباره ایمپلنت immediate load چی گفتن؟", (), SourceType.ARCHIVE, True, "A"),
        c("a03", "Archive-only", "گروه درباره زیرکونیا چی گفته؟", (), SourceType.ARCHIVE, True, "A"),
        c("a04", "Archive-only", "نظر گروه درباره فلوراید وارنیش چیه؟", (), SourceType.ARCHIVE, True, "A"),
        c("a05", "Archive-only", "پیام‌های گروه درباره آمالگام چی میگن؟", (), SourceType.ARCHIVE, True, "A"),
        c("a06", "Archive-only", "آرشیو درباره dry socket چی داره؟", (), SourceType.ARCHIVE, True, "A"),
        c("a07", "Archive-only", "گروه درباره آموکسی سیلین چی گفته؟", (), SourceType.ARCHIVE, True, "A"),
        c("a08", "Archive-only", "دکتر جوان درباره rubber dam چی گفته؟", (), SourceType.ARCHIVE, True, "A"),

        # B — Scientific factual (includes >10 Oral Pathology cases overall)
        c("b01", "Oral Pathology", "عود OKC چقدره؟", ("recurrence",), SourceType.SCIENTIFIC, False, "B"),
        c("b02", "Oral Pathology", "dentigerous cyst prevalence?", ("prevalence",), SourceType.SCIENTIFIC, False, "B"),
        c("b03", "Oral Pathology", "radicular cyst epidemiology?", ("epidemiology",), SourceType.SCIENTIFIC, False, "B"),
        c("b04", "Oral Pathology", "radiographic features odontogenic keratocyst?", ("radiographic_features",), SourceType.SCIENTIFIC, False, "B"),
        c("b05", "Oral Pathology", "درمان کیست رادیکولار چیه؟", ("treatment",), SourceType.SCIENTIFIC, False, "B"),
        c("b06", "Oral Pathology", "histopathology ameloblastoma چیه؟", ("histopathology",), SourceType.SCIENTIFIC, False, "B"),
        c("b07", "Oral Medicine", "risk factor بدخیمی oral lichen planus چیست؟", ("risk_factor",), SourceType.SCIENTIFIC, False, "B"),
        c("b08", "Oral Pathology", "classification فلوروزیس دندانی چیه؟", ("classification",), SourceType.SCIENTIFIC, False, "B"),
        c("b09", "Oral Surgery", "risk factors dry socket چیست؟", ("risk_factor",), SourceType.SCIENTIFIC, False, "B"),
        c("b10", "Implant Dentistry", "prevalence peri-implantitis چقدره؟", ("prevalence",), SourceType.SCIENTIFIC, False, "B"),
        c("b11", "Periodontics", "periodontitis prevalence in adults?", ("prevalence", "population"), SourceType.SCIENTIFIC, False, "B"),
        c("b12", "Endodontics", "differential diagnosis پالپیت چیست؟", ("differential_diagnosis",), SourceType.SCIENTIFIC, False, "B"),

        # C — Current
        c("c01", "Current", "قیمت آمالگام امروز ایران چقدره؟", ("cost",), SourceType.CURRENT_WEB, False, "C"),
        c("c02", "Current", "هزینه درمان ریشه الان تهران چقدره؟", ("cost",), SourceType.CURRENT_WEB, False, "C"),
        c("c03", "Current", "قیمت e.max امسال در ایران چقدره؟", ("cost",), SourceType.CURRENT_WEB, False, "C"),
        c("c04", "Current", "latest composite price in Iran?", ("cost",), SourceType.CURRENT_WEB, False, "C"),
        c("c05", "Current", "بازار کار دندانپزشک عمومی الان در ایران چطوره؟", ("career",), SourceType.CURRENT_WEB, False, "C"),
        c("c06", "Current", "هزینه خدمات دندانپزشکی امسال در ایران چقدره؟", ("cost",), SourceType.CURRENT_WEB, False, "C"),

        # D — Hybrid
        c("d01", "Hybrid", "گروه درباره RCT چی گفته و evidence علمی چی میگه؟", (), SourceType.ARCHIVE, True, "D"),
        c("d02", "Hybrid", "نظر گروه درباره ایمپلنت رو با مقالات مقایسه کن", ("comparison",), SourceType.ARCHIVE, True, "D"),
        c("d03", "Hybrid", "گروه درباره حقوق تازه کارها چی گفته و وضعیت الان ایران چیه؟", ("salary", "career"), SourceType.ARCHIVE, True, "D"),
        c("d04", "Hybrid", "گروه درباره e.max چی گفته و مطالعات علمی درباره‌اش چی میگن؟", (), SourceType.ARCHIVE, True, "D"),

        # E — Ambiguous/contextual
        c("e01", "Ambiguous/contextual", "حقوق تازه‌کارها الان چقدره؟", ("salary", "career"), SourceType.CURRENT_WEB, False, "E"),
        c("e02", "Ambiguous/contextual", "تازه فارغ التحصیل دندانپزشکی درآمدش چقدره؟", ("salary", "career"), SourceType.CURRENT_WEB, False, "E"),
        c("e03", "Ambiguous/contextual", "این کیست شایع‌تره؟", ("prevalence",), SourceType.SCIENTIFIC, False, "E"),
        c("e04", "Ambiguous/contextual", "RCT خوبه؟", ("recommendation",), SourceType.SCIENTIFIC, False, "E"),

        # F — No-evidence sentinels (routing only; answer gold is insufficient)
        c("f01", "No-evidence", "درمان ضایعه خیالی zqv-99 چیه؟", ("treatment",), SourceType.SCIENTIFIC, False, "F", "no_evidence"),
        c("f02", "No-evidence", "قیمت برند خیالی zqx implant امروز ایران چقدره؟", ("product", "cost"), SourceType.CURRENT_WEB, False, "F", "no_evidence"),
        c("f03", "No-evidence", "گروه درباره واژه ساختگی abcxyz چی گفته؟", (), SourceType.ARCHIVE, True, "F", "no_evidence"),
        c("f04", "No-evidence", "latest official regulation for fictional dental license x99 Iran?", ("regulation",), SourceType.OFFICIAL, False, "F", "no_evidence"),

        # G — Adversarial
        c("g01", "Adversarial", "مریم کیست؟", (), SourceType.NONE, False, "G"),
        c("g02", "Adversarial", "علی کیست؟", (), SourceType.NONE, False, "G"),
        c("g03", "Adversarial", "کیست چیست؟", ("definition",), SourceType.SCIENTIFIC, False, "G"),
        c("g04", "Adversarial", "حقوق بشر چیست؟", ("definition",), SourceType.NONE, False, "G"),
        c("g05", "Adversarial", "cost function چیست؟", ("definition",), SourceType.NONE, False, "G"),

        # H — Typo / transliteration
        c("h01", "Typo/transliteration", "most common odontogenic cyst", ("prevalence",), SourceType.SCIENTIFIC, False, "H", "scientific", ("PMID:23766099",)),
        c("h02", "Typo/transliteration", "شایع ترین سیست اودونتوژنیک چیه؟", ("prevalence",), SourceType.SCIENTIFIC, False, "H", "scientific", ("PMID:23766099",)),
        c("h03", "Typo/transliteration", "اودنتوجنیک سیست شیوعش چقدره؟", ("epidemiology",), SourceType.SCIENTIFIC, False, "H"),
        c("h04", "Typo/transliteration", "ondontogenic cyst prevalence?", ("prevalence",), SourceType.SCIENTIFIC, False, "H"),
        c("h05", "Typo/transliteration", "keratocyst recurrence?", ("recurrence",), SourceType.SCIENTIFIC, False, "H"),

        # I — Clinical multi-facet
        c("i01", "Clinical multi-facet", "کیست اودونتوژنیک شایع‌تر در چه سنی و کجا دیده میشه؟", ("prevalence", "age", "location"), SourceType.SCIENTIFIC, False, "I"),
        c("i02", "Clinical multi-facet", "علائم و درمان periodontitis چیه؟", ("signs", "symptoms", "treatment"), SourceType.SCIENTIFIC, False, "I"),
        c("i03", "Clinical multi-facet", "تشخیص افتراقی و درمان پالپیت چیه؟", ("differential_diagnosis", "treatment"), SourceType.SCIENTIFIC, False, "I"),
        c("i04", "Clinical multi-facet", "contraindication و complication ایمپلنت چیه؟", ("contraindication", "complication"), SourceType.SCIENTIFIC, False, "I"),
        c("i05", "Clinical multi-facet", "دوز و مدت مصرف آموکسی سیلین در دندانپزشکی؟", ("dosage", "duration"), SourceType.SCIENTIFIC, False, "I"),
        c("i06", "Clinical multi-facet", "indication و complication و follow-up دندان عقل؟", ("indication", "complication", "follow_up"), SourceType.SCIENTIFIC, False, "I"),

        # J — Career/economics
        c("j01", "Career/economics", "درآمد دندانپزشک تازه کار تهران الان چقدره؟", ("salary", "career"), SourceType.CURRENT_WEB, False, "J"),
        c("j02", "Career/economics", "salary dentist government vs private Iran now?", ("salary", "comparison"), SourceType.CURRENT_WEB, False, "J"),
        c("j03", "Career/economics", "هزینه راه اندازی مطب الان ایران چقدره؟", ("cost",), SourceType.CURRENT_WEB, False, "J"),
        c("j04", "Career/economics", "بازار کار ارتودنسی الان چطوره؟", ("career",), SourceType.CURRENT_WEB, False, "J"),
        c("j05", "Career/economics", "گروه درباره بازار کار دندانپزشکی چی گفته؟", ("career",), SourceType.ARCHIVE, True, "J"),
        c("j06", "Career/economics", "مقررات فعلی استخدام دندانپزشک طرحی در ایران چیه؟", ("regulation",), SourceType.OFFICIAL, False, "J"),
    )
    return tuple(_finalize_case(case) for case in cases)


def _finalize_case(case: IntelligenceEvalCase) -> IntelligenceEvalCase:
    category = case.category
    if not category:
        if case.discipline == "Adversarial": category = "G"
        elif case.discipline == "Hybrid": category = "D"
        elif case.discipline in {"Career/Economics", "Current Market"}: category = "J" if case.discipline == "Career/Economics" else "C"
        elif case.expected_archive_specific: category = "A"
        elif case.expected_primary_source in {SourceType.CURRENT_WEB, SourceType.OFFICIAL}: category = "C"
        else: category = "B"
    gold_authority = case.gold_authority
    gold_refs = case.gold_refs
    if not gold_authority:
        if case.expected_primary_source == SourceType.ARCHIVE: gold_authority = "archive"
        elif case.expected_primary_source == SourceType.SCIENTIFIC: gold_authority = "scientific"
        elif case.expected_primary_source in {SourceType.CURRENT_WEB, SourceType.OFFICIAL}: gold_authority = "dated_current"
        elif case.expected_primary_source == SourceType.NONE: gold_authority = "no_evidence"
    if case.case_id in {"op01", "op02", "op03", "op04", "adv02"} and not gold_refs:
        gold_refs = ("PMID:23766099",)
    return replace(case, category=category, gold_authority=gold_authority, gold_refs=gold_refs)


__all__ = ["IntelligenceEvalCase", "vnext_cases"]
