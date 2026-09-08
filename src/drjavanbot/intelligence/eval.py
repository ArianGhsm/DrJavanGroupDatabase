from __future__ import annotations

from dataclasses import dataclass

from .models import SourceType


@dataclass(frozen=True, slots=True)
class IntelligenceEvalCase:
    case_id: str
    discipline: str
    question: str
    expected_facets: tuple[str, ...]
    expected_primary_source: str
    expected_archive_specific: bool = False


def vnext_cases() -> tuple[IntelligenceEvalCase, ...]:
    c = IntelligenceEvalCase
    return (
        c("op01", "Oral Pathology", "کدام کیست‌های اودونتوژنیک رایج‌تر هستند؟", ("prevalence",), SourceType.DENTAL_KNOWLEDGE),
        c("op02", "Oral Pathology", "شایع‌ترین کیست ادنتوژنیک چیست؟", ("prevalence",), SourceType.DENTAL_KNOWLEDGE),
        c("op03", "Oral Pathology", "most common odontogenic cyst?", ("prevalence",), SourceType.DENTAL_KNOWLEDGE),
        c("op04", "Oral Pathology", "کدوم سیست اودنتوژنیک بیشتر دیده میشه؟", ("prevalence",), SourceType.DENTAL_KNOWLEDGE),
        c("op05", "Oral Pathology", "طبقه بندی کیست های فکی چیه؟", ("classification",), SourceType.DENTAL_KNOWLEDGE),
        c("op06", "Oral Pathology", "عود این ضایعه چقدره؟", ("recurrence",), SourceType.DENTAL_KNOWLEDGE),
        c("om01", "Oral Medicine", "تشخیص افتراقی این ضایعه دهانی چیه؟", ("differential_diagnosis",), SourceType.DENTAL_KNOWLEDGE),
        c("om02", "Oral Medicine", "علائم این بیماری دهان چی هستند؟", ("signs", "symptoms"), SourceType.DENTAL_KNOWLEDGE),
        c("om03", "Oral Medicine", "risk factor های این مشکل دهانی چیان؟", ("risk_factor",), SourceType.DENTAL_KNOWLEDGE),
        c("om04", "Oral Medicine", "برای این وضعیت چه درمانی پیشنهاد میشه؟", ("treatment",), SourceType.DENTAL_KNOWLEDGE),
        c("rad01", "Oral Radiology", "radiographic features این ضایعه چیه؟", ("radiographic_features",), SourceType.DENTAL_KNOWLEDGE),
        c("rad02", "Oral Radiology", "در رادیوگرافی تشخیصش چیه؟", ("radiographic_features", "diagnosis"), SourceType.DENTAL_KNOWLEDGE),
        c("rad03", "Oral Radiology", "این نمای x-ray با اون یکی چه مقایسه‌ای داره؟", ("radiographic_features", "comparison"), SourceType.DENTAL_KNOWLEDGE),
        c("rad04", "Oral Radiology", "محل شایع این یافته radiographic کجاست؟", ("location", "prevalence", "radiographic_features"), SourceType.DENTAL_KNOWLEDGE),
        c("endo01", "Endodontics", "diagnosis پالپیت چطور انجام میشه؟", ("diagnosis", "method"), SourceType.DENTAL_KNOWLEDGE),
        c("endo02", "Endodontics", "تکنیک working length رو توضیح بده", ("technique",), SourceType.DENTAL_KNOWLEDGE),
        c("endo03", "Endodontics", "عوارض RCT چی میتونه باشه؟", ("complication",), SourceType.DENTAL_KNOWLEDGE),
        c("endo04", "Endodontics", "prognosis درمان ریشه چطوره؟", ("prognosis",), SourceType.DENTAL_KNOWLEDGE),
        c("perio01", "Periodontics", "تعریف periodontitis چیه؟", ("definition",), SourceType.DENTAL_KNOWLEDGE),
        c("perio02", "Periodontics", "اندیکاسیون جراحی پریو چیه؟", ("indication",), SourceType.DENTAL_KNOWLEDGE),
        c("perio03", "Periodontics", "contraindication این درمان پریو چیه؟", ("contraindication",), SourceType.DENTAL_KNOWLEDGE),
        c("perio04", "Periodontics", "فالوآپ بعد درمان لثه چه زمانی باشه؟", ("follow_up", "timing"), SourceType.DENTAL_KNOWLEDGE),
        c("rest01", "Restorative", "کامپوزیت یا آمالگام کدوم بهتره؟", ("comparison",), SourceType.DENTAL_KNOWLEDGE),
        c("rest02", "Restorative", "روش bonding رو چطور انجام میدن؟", ("method",), SourceType.DENTAL_KNOWLEDGE),
        c("rest03", "Restorative", "اندیکاسیون گلاس آینومر چیه؟", ("indication",), SourceType.DENTAL_KNOWLEDGE),
        c("rest04", "Restorative", "چه material ای برای این ترمیم مناسبه؟", ("material",), SourceType.DENTAL_KNOWLEDGE),
        c("pros01", "Prosthodontics", "e.max و زیرکونیا رو مقایسه کن", ("comparison",), SourceType.DENTAL_KNOWLEDGE),
        c("pros02", "Prosthodontics", "تکنیک قالبگیری پروتز چیه؟", ("technique",), SourceType.DENTAL_KNOWLEDGE),
        c("pros03", "Prosthodontics", "عوارض روکش چی هست؟", ("complication",), SourceType.DENTAL_KNOWLEDGE),
        c("pros04", "Prosthodontics", "گروه درباره e.max چی گفته؟", (), SourceType.ARCHIVE, True),
        c("ortho01", "Orthodontics", "برای بچه ها ارتودنسی از چه سنی؟", ("age", "population"), SourceType.DENTAL_KNOWLEDGE),
        c("ortho02", "Orthodontics", "indication ارتودنسی چیه؟", ("indication",), SourceType.DENTAL_KNOWLEDGE),
        c("ortho03", "Orthodontics", "fixed vs removable orthodontics رو مقایسه کن", ("comparison",), SourceType.DENTAL_KNOWLEDGE),
        c("ortho04", "Orthodontics", "duration درمان ارتودنسی چقدره؟", ("duration",), SourceType.DENTAL_KNOWLEDGE),
        c("pedo01", "Pediatric Dentistry", "دوز دارو برای کودک چقدره؟", ("dosage", "population"), SourceType.DENTAL_KNOWLEDGE),
        c("pedo02", "Pediatric Dentistry", "زمان درمان پالپ در بچه ها کیه؟", ("timing", "population", "treatment"), SourceType.DENTAL_KNOWLEDGE),
        c("pedo03", "Pediatric Dentistry", "contraindication پالپوتومی در کودک؟", ("contraindication", "population"), SourceType.DENTAL_KNOWLEDGE),
        c("pedo04", "Pediatric Dentistry", "prognosis درمان دندان شیری؟", ("prognosis",), SourceType.DENTAL_KNOWLEDGE),
        c("surg01", "Oral Surgery", "اندیکاسیون کشیدن دندان عقل چیه؟", ("indication",), SourceType.DENTAL_KNOWLEDGE),
        c("surg02", "Oral Surgery", "عوارض بعد جراحی دهان چیان؟", ("complication",), SourceType.DENTAL_KNOWLEDGE),
        c("surg03", "Oral Surgery", "چه زمانی باید follow-up بشه؟", ("timing", "follow_up"), SourceType.DENTAL_KNOWLEDGE),
        c("surg04", "Oral Surgery", "روش انجام این جراحی چیه؟", ("method",), SourceType.DENTAL_KNOWLEDGE),
        c("impl01", "Implant Dentistry", "contraindication ایمپلنت چیه؟", ("contraindication",), SourceType.DENTAL_KNOWLEDGE),
        c("impl02", "Implant Dentistry", "success/prognosis ایمپلنت چطوره؟", ("prognosis",), SourceType.DENTAL_KNOWLEDGE),
        c("impl03", "Implant Dentistry", "قیمت ایمپلنت الان در ایران چقدره؟", ("cost",), SourceType.CURRENT_WEB),
        c("impl04", "Implant Dentistry", "گروه درباره برند ایمپلنت چی پیشنهاد داده؟", ("product", "recommendation"), SourceType.ARCHIVE, True),
        c("pharm01", "Pharmacology", "دوز آموکسی سیلین در دندانپزشکی چقدره؟", ("dosage",), SourceType.DENTAL_KNOWLEDGE),
        c("pharm02", "Pharmacology", "contraindication این دارو چیه؟", ("contraindication",), SourceType.DENTAL_KNOWLEDGE),
        c("pharm03", "Pharmacology", "عوارض این antibiotic چیه؟", ("complication",), SourceType.DENTAL_KNOWLEDGE),
        c("pharm04", "Pharmacology", "guideline جدید آنتی بیوتیک پروفیلاکسی چیه؟", ("guideline",), SourceType.DENTAL_KNOWLEDGE),
        c("career01", "Career/Economics", "حقوق دانشجوهای تازه فارغ التحصیل شده چقدره؟", ("salary", "career"), SourceType.CURRENT_WEB),
        c("career02", "Career/Economics", "حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟", ("salary", "career"), SourceType.CURRENT_WEB),
        c("career03", "Career/Economics", "salary new graduate dentist in Iran?", ("salary", "career"), SourceType.CURRENT_WEB),
        c("career04", "Career/Economics", "بازار کار دندانپزشکی الان چطوره؟", ("career",), SourceType.CURRENT_WEB),
        c("career05", "Career/Economics", "گروه درباره حقوق دندانپزشکا چی گفته؟", ("salary",), SourceType.ARCHIVE, True),
        c("career06", "Career/Economics", "درباره حقوق دندانپزشک تازه‌کار هم نظر گروه رو بگو هم وضعیت الان ایران رو", ("salary",), SourceType.ARCHIVE, True),
        c("reg01", "Regulation", "قانون جدید مجوز مطب دندانپزشکی چیه؟", ("regulation",), SourceType.CURRENT_WEB),
        c("reg02", "Regulation", "مقررات فعلی تبلیغات دندانپزشکی در ایران؟", ("regulation",), SourceType.CURRENT_WEB),
        c("reg03", "Regulation", "official regulation for dental clinic license in Iran", ("regulation",), SourceType.CURRENT_WEB),
        c("current01", "Current Market", "قیمت کامپوزیت امروز تو ایران چنده؟", ("cost",), SourceType.CURRENT_WEB),
        c("current02", "Current Market", "latest price e.max ایران", ("cost",), SourceType.CURRENT_WEB),
        c("hybrid01", "Hybrid", "بچه های گروه درباره e.max چی گفتن و مقالات چی میگن؟", (), SourceType.ARCHIVE, True),
        c("hybrid02", "Hybrid", "نظر گروه درباره کامپوزیت رو با evidence علمی مقایسه کن", ("comparison",), SourceType.ARCHIVE, True),
        c("adv01", "Adversarial", "علی کیست؟", (), SourceType.NONE),
        c("adv02", "Adversarial", "odontogenic cyst prevalence؟", ("prevalence",), SourceType.DENTAL_KNOWLEDGE),
    )


__all__ = ["IntelligenceEvalCase", "vnext_cases"]
