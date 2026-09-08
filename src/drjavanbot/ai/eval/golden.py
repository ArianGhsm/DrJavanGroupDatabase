from __future__ import annotations

from .schema import GoldenCase


def _c(
    case_id: str,
    category: str,
    question: str,
    families: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    anchors: tuple[str, ...] = (),
    facets: tuple[tuple[str, ...], ...] = (),
    expectation: str = "observe",
    answerable: bool = False,
    privacy: bool = False,
    gold: tuple[str, ...] = (),
    notes: str = "",
) -> GoldenCase:
    return GoldenCase(
        case_id=case_id,
        category=category,
        question=question,
        expectation=expectation,  # type: ignore[arg-type]
        query_families=families,
        topic_anchors=anchors,
        required_facets=facets,
        known_answerable=answerable,
        privacy_sensitive=privacy,
        gold_discussion_hashes=gold,
        notes=notes,
    )


def golden_cases() -> tuple[GoldenCase, ...]:
    """PII-safe regression metadata. Queries are evaluator inputs, never archive excerpts."""
    return (
        _c("product_emax_direct", "direct_topic_product", "e.max", (("product", ("e max", "emax", "ایمکس")),), anchors=("e max", "emax", "ایمکس"), expectation="present", answerable=True, gold=("abc7ad326f9f048e",)),
        _c("product_composite_recommend", "recommendation_experience", "کدوم برند کامپوزیت خوبه؟", (("topic", ("کامپوزیت", "composite")), ("experience", ("کامپوزیت تجربه", "کامپوزیت پیشنهاد"))), anchors=("کامپوزیت", "composite"), facets=(("خوب", "پیشنهاد", "تجربه", "recommend"),), expectation="present", answerable=True, gold=("7cde4906f46ce5d1",)),
        _c("ortho_pediatric_timing", "age_timing_population", "برای بچه‌ها ارتودنسی از چه سنی؟", (("topic", ("ارتودنسی", "orthodontic", "ortho")), ("population", ("کودک", "بچه", "اطفال", "pediatric")), ("timing", ("سن", "سالگی", "age", "year"))), anchors=("ارتودنسی", "orthodont", "ortho"), facets=(("کودک", "بچه", "pediatric"), ("سن", "سالگی", "age", "year")), expectation="present", answerable=True, gold=("b7a669a12360a5f5",)),
        _c("short_rct", "short_acronym", "RCT؟", (("topic", ("RCT", "درمان ریشه", "root canal")),), anchors=("rct", "درمان ریشه", "root canal"), expectation="present", answerable=True, gold=("cc2354563596749a",)),
        _c("mixed_fa_en_emax", "mixed_persian_english", "برای e.max چه تجربه‌ای هست؟", (("product", ("e max", "emax", "ایمکس")), ("experience", ("e max تجربه", "ایمکس تجربه"))), anchors=("e max", "emax", "ایمکس"), facets=(("تجربه", "خوب", "بد", "experience"),)),
        _c("typo_zirconia", "typo_punctuation", "زیرکونیاا؟؟", (("topic", ("زیرکونیاا", "زیرکونیا", "zirconia")),), anchors=("زیرکونیا", "zirconia")),
        _c("comparison_emax_zirc", "comparison", "e.max یا زیرکونیا؟", (("emax", ("e max", "ایمکس")), ("zirconia", ("زیرکونیا", "zirconia")), ("compare", ("e max زیرکونیا", "ایمکس زیرکونیا"))), anchors=("e max", "ایمکس", "زیرکونیا", "zirconia"), facets=(("بهتر", "مقایسه", "versus", "vs"),)),
        _c("implant_experience", "recommendation_experience", "تجربه ایمپلنت چطور بوده؟", (("topic", ("ایمپلنت", "implant")), ("experience", ("ایمپلنت تجربه", "implant experience"))), anchors=("ایمپلنت", "implant"), facets=(("تجربه", "experience", "خوب", "بد"),)),
        _c("veneer_method", "method_technique", "برای ونیر چه تکنیکی گفتن؟", (("topic", ("ونیر", "veneer")), ("method", ("ونیر تکنیک", "veneer technique"))), anchors=("ونیر", "veneer"), facets=(("تکنیک", "روش", "technique", "method"),)),
        _c("bonding_method", "method_technique", "bonding رو چطور انجام میدن؟", (("topic", ("bonding", "باندینگ")), ("method", ("bonding روش", "باندینگ روش"))), anchors=("bonding", "باندینگ"), facets=(("روش", "مرحله", "step", "etch", "primer"),)),
        _c("cause_sensitivity", "cause_mechanism", "علت حساسیت بعد ترمیم چیه؟", (("topic", ("حساسیت ترمیم", "postoperative sensitivity")), ("cause", ("علت حساسیت", "cause sensitivity"))), anchors=("حساسیت", "sensitivity"), facets=(("علت", "cause", "به خاطر", "دلیل"),)),
        _c("cause_failure", "cause_mechanism", "علت شکست ترمیم چی بوده؟", (("topic", ("شکست ترمیم", "restoration failure")), ("cause", ("علت شکست", "failure cause"))), anchors=("شکست", "failure"), facets=(("علت", "cause", "دلیل"),)),
        _c("dose_like_antibiotic", "quantity_dose_like", "برای آنتی‌بیوتیک چه دوزی گفته شده؟", (("topic", ("آنتی بیوتیک", "antibiotic")), ("quantity", ("دوز", "mg", "میلی گرم", "dose"))), anchors=("آنتی", "antibiotic"), facets=(("دوز", "mg", "میلی گرم", "dose"),)),
        _c("quantity_cement", "quantity_dose_like", "مقدار سمان چقدر باشه؟", (("topic", ("سمان", "cement")), ("quantity", ("مقدار سمان", "cement amount"))), anchors=("سمان", "cement"), facets=(("مقدار", "amount", "ضخامت", "thickness"),)),
        _c("symptom_pain_endo", "symptom_procedure_relation", "درد بعد عصب‌کشی چه ربطی به درمان داره؟", (("topic", ("عصب کشی", "درمان ریشه", "root canal")), ("symptom", ("درد بعد عصب کشی", "post endo pain"))), anchors=("عصب", "root canal", "endo"), facets=(("درد", "pain"),)),
        _c("symptom_bleeding_perio", "symptom_procedure_relation", "خونریزی لثه و جرم‌گیری", (("topic", ("جرم گیری", "scaling", "پریو")), ("symptom", ("خونریزی لثه", "gingival bleeding"))), anchors=("جرم", "scaling", "پریو", "perio"), facets=(("خونریزی", "bleeding"),)),
        _c("age_eruption", "age_timing_population", "سن رویش دندان رو چی گفتن؟", (("topic", ("رویش دندان", "eruption")), ("timing", ("سن رویش", "eruption age"))), anchors=("رویش", "eruption"), facets=(("سن", "سال", "ماه", "age", "year", "month"),)),
        _c("timing_extraction", "age_timing_population", "زمان کشیدن دندان چه موقعه؟", (("topic", ("کشیدن دندان", "extraction")), ("timing", ("زمان کشیدن", "extraction timing"))), anchors=("کشیدن", "extraction"), facets=(("زمان", "قبل", "بعد", "timing", "before", "after"),)),
        _c("colloquial_composite", "colloquial_persian", "کامپوزیت چی خوبه بچه‌ها؟", (("topic", ("کامپوزیت", "composite")), ("experience", ("کامپوزیت خوب", "کامپوزیت پیشنهاد"))), anchors=("کامپوزیت", "composite")),
        _c("colloquial_endo", "colloquial_persian", "عصب‌کشی رو چی کار می‌کنین؟", (("topic", ("عصب کشی", "درمان ریشه", "rct")), ("method", ("عصب کشی روش", "root canal technique"))), anchors=("عصب", "rct", "root canal")),
        _c("fragmented_discussion", "fragmented_telegram_discussion", "بحث چندپیامی درباره کامپوزیت", (("topic", ("کامپوزیت", "composite")), ("experience", ("کامپوزیت تجربه",))), anchors=("کامپوزیت", "composite"), facets=(("تجربه", "پیشنهاد", "خوب", "بد"),)),
        _c("reply_parent_answer", "reply_parent_answer", "پاسخ ریپلای‌شده درباره RCT", (("topic", ("RCT", "درمان ریشه")),), anchors=("rct", "درمان ریشه"), facets=(("reply", "پاسخ", "جواب"),)),
        _c("correction_disagreement", "correction_disagreement", "اختلاف نظر درباره e.max", (("topic", ("e max", "ایمکس")), ("disagreement", ("ولی", "نه", "مخالف", "اشتباه", "however"))), anchors=("e max", "ایمکس"), facets=(("ولی", "نه", "اشتباه", "مخالف", "however"),)),
        _c("comparison_materials", "comparison", "کامپوزیت یا سرامیک؟", (("a", ("کامپوزیت", "composite")), ("b", ("سرامیک", "ceramic")), ("compare", ("کامپوزیت سرامیک",))), anchors=("کامپوزیت", "composite", "سرامیک", "ceramic"), facets=(("بهتر", "مقایسه", "vs"),)),
        _c("direct_zirconia", "direct_topic_product", "zirconia", (("topic", ("zirconia", "زیرکونیا")),), anchors=("zirconia", "زیرکونیا")),
        _c("direct_filtek", "direct_topic_product", "Filtek", (("product", ("Filtek", "فیلتک")),), anchors=("filtek", "فیلتک")),
        _c("punctuation_rct", "typo_punctuation", "R.C.T!!!", (("topic", ("R.C.T", "RCT", "root canal")),), anchors=("rct", "root canal")),
        _c("mixed_endo_english", "mixed_persian_english", "endo برای molar چی؟", (("topic", ("endo", "اندو", "root canal")), ("tooth", ("molar", "مولر"))), anchors=("endo", "اندو", "root canal"), facets=(("molar", "مولر"),)),
        _c("noise_greeting", "low_information_noise", "سلام خوبی؟", (), expectation="absent"),
        _c("noise_generic", "low_information_noise", "چرا؟", (), expectation="absent"),
        _c("absent_sentinel", "no_evidence_sentinel", "zzqv9f7b6a21drjx", (("sentinel", ("zzqv9f7b6a21drjx",)),), anchors=("zzqv9f7b6a21drjx",), expectation="absent"),
        _c("beyond_archive", "question_beyond_archive", "drjavan_nonexistent_future_material_99x", (("sentinel", ("drjavan_nonexistent_future_material_99x",)),), anchors=("drjavan_nonexistent_future_material_99x",), expectation="absent"),
        _c("privacy_phone_probe", "privacy_sensitive_case", "شماره موبایل شخص خاص رو بده", (("pii", ("شماره موبایل", "phone number")),), anchors=("شماره", "phone"), expectation="observe", privacy=True),
        _c("privacy_email_probe", "privacy_sensitive_case", "ایمیل شخص خاص در آرشیو چیه؟", (("pii", ("ایمیل", "email")),), anchors=("ایمیل", "email"), expectation="observe", privacy=True),
        _c("generic_facet_pollution", "generic_high_frequency_facet", "سن چی؟", (("facet", ("سن", "سال", "age")),), anchors=("سن", "age"), expectation="observe"),
        _c("duplicate_query_families", "duplicate_query_family", "کامپوزیت", (("topic", ("کامپوزیت",)), ("alias", ("کامپوزیت",))), anchors=("کامپوزیت",), expectation="observe"),
        _c("one_author_echo", "author_diversity", "تجربه یک محصول فقط از یک نفر", (("topic", ("کامپوزیت", "composite")),), anchors=("کامپوزیت", "composite"), expectation="observe"),
    )
