from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Sequence

from drjavanbot.normalization import normalize_text

from .models import AnswerResult, ClaimSupport, EvidencePack, GroundedClaim
from .validation import CitationValidationError, ModelOutputError, parse_json_object


VALIDATION_SEMANTICS_VERSION = "claim-support-validation-v2.1"
_ALLOWED_KINDS = {"answer", "finding", "disagreement", "conclusion"}
_NEGATION_RE = re.compile(r"(?:\bnot\b|\bno\b|\bnever\b|نیست|نبود|نمی|نکن|ندارد|نداره|بدون)", re.IGNORECASE)
_COMPARISON_MARKERS = (
    "بهتر از", "بدتر از", "بیشتر از", "کمتر از", "قوی تر از", "قوی‌تر از",
    "ضعیف تر از", "ضعیف‌تر از", "better than", "worse than", "more than", "less than",
)
_CLINICAL_RE = re.compile(
    r"(?:درمان|تشخیص|دوز|دارو|تجویز|منع مصرف|کنتراندیک|جراحی|کشیدن|آنتی.?بیوتیک|therapy|diagnos|dose|contraindicat)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?<![\w])[-+]?\d+(?:[.,٫]\d+)?(?:\s*[%٪])?(?![\w])|[۰-۹]+(?:[٫.,][۰-۹]+)?")
_LATIN_OR_MODEL_RE = re.compile(r"\b(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*(?:\d|[A-Z]))[A-Za-z][A-Za-z0-9_-]*\b")
_TOKEN_RE = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)
_FRAMING = {
    "در", "گروه", "پیام", "پیامها", "پیام‌های", "گفته", "شده", "است", "بود", "هست", "مطرح",
    "طبق", "بر اساس", "آرشیو", "این", "آن", "یک", "هم", "و", "یا", "که", "از", "به", "برای",
    "the", "a", "an", "in", "archive", "message", "messages", "group", "was", "is", "were",
}
_CORRECTION_MARKERS = ("اصلاح", "اشتباه", "درستش", "برعکس", "اما", "ولی", "نه ", "correction", "wrong", "however")
_NUMBER_WORDS = (
    "صفر", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه", "ده", "یازده", "دوازده",
    "سالگی", "ماهگی", "میلی", "گرم", "دوز",
)


@dataclass(frozen=True, slots=True)
class AnswerabilityAssessment:
    answerable: bool
    reason_code: str
    topic_anchored: bool
    facet_coverage: float
    evidence_directness: str
    discussion_coherence: bool
    independent_authors: int
    correction_or_conflict: bool
    requested_fact_signal: bool
    evidence_count: int

    def to_public_dict(self) -> dict[str, object]:
        """Bounded signals only; never raw evidence or private reasoning."""
        return {
            "answerable": self.answerable,
            "reason_code": self.reason_code,
            "topic_anchored": self.topic_anchored,
            "facet_coverage": round(self.facet_coverage, 3),
            "evidence_directness": self.evidence_directness,
            "discussion_coherence": self.discussion_coherence,
            "independent_authors": self.independent_authors,
            "correction_or_conflict": self.correction_or_conflict,
            "requested_fact_signal": self.requested_fact_signal,
            "evidence_count": self.evidence_count,
        }


@dataclass(frozen=True, slots=True)
class ClaimCandidate:
    claim: GroundedClaim
    risk_flags: tuple[str, ...]
    needs_semantic_verification: bool


@dataclass(frozen=True, slots=True)
class ClaimExtraction:
    insufficient_evidence: bool
    claims: tuple[ClaimCandidate, ...]


@dataclass(frozen=True, slots=True)
class VerificationVerdict:
    claim_index: int
    entailed: bool
    risk_ok: bool


def assess_answerability(pack: EvidencePack, plan, report) -> AnswerabilityAssessment:
    if not pack.messages:
        return AnswerabilityAssessment(False, "no_candidates", False, 0.0, "none", False, 0, False, False, 0)

    normalized_messages = [normalize_text(item.text or "") for item in pack.messages]
    concepts = tuple(getattr(plan, "core_concepts", ()) or ()) + tuple(getattr(plan, "aliases", ()) or ())
    concept_terms = _concept_terms(concepts)
    if concept_terms:
        topic_anchored = any(any(term in text for term in concept_terms) for text in normalized_messages)
    else:
        topic_anchored = bool(pack.messages)

    required = tuple(getattr(plan, "required_aspects", ()) or ())
    hit_names = tuple(str(value) for value in (getattr(report, "hit_family_names", ()) or ()))
    family_names = tuple(str(getattr(value, "name", "")) for value in (getattr(plan, "query_families", ()) or ()))
    covered = 0
    for aspect in required:
        if _aspect_is_covered(str(aspect), topic_anchored=topic_anchored, hit_names=hit_names, family_names=family_names):
            covered += 1
    facet_coverage = (covered / len(required)) if required else (1.0 if topic_anchored else 0.0)

    direct_reasons = {"exact_phrase", "normalized_tokens", "phrase_match", "reply_context", "conversation_bridge"}
    direct_hits = sum(1 for item in pack.messages if set(item.match_reasons) & direct_reasons)
    if direct_hits >= 2:
        directness = "direct"
    elif direct_hits == 1:
        directness = "mixed"
    else:
        directness = "contextual"

    coherence = bool(
        int(getattr(report, "conversation_bridges", 0) or 0)
        or int(getattr(report, "discussion_windows", 0) or 0)
        or any(item.parent_source_ref or item.role != "evidence" for item in pack.messages)
    )
    authors = len({item.author for item in pack.messages if item.author})
    lower_joined = "\n".join(normalized_messages).casefold()
    correction = any(marker in lower_joined for marker in _CORRECTION_MARKERS)
    requested_fact_signal = _requested_fact_signal(required, lower_joined, direct_hits)

    if not topic_anchored:
        answerable = False
        reason = "topic_found_facet_missing"
    elif required and facet_coverage < 0.5 and not (coherence and requested_fact_signal):
        answerable = False
        reason = "topic_found_facet_missing"
    elif correction and authors >= 2 and directness == "contextual" and not requested_fact_signal:
        answerable = False
        reason = "conflicting_only"
    else:
        answerable = True
        reason = "fragmented_but_answerable"

    return AnswerabilityAssessment(
        answerable=answerable,
        reason_code=reason,
        topic_anchored=topic_anchored,
        facet_coverage=facet_coverage,
        evidence_directness=directness,
        discussion_coherence=coherence,
        independent_authors=authors,
        correction_or_conflict=correction,
        requested_fact_signal=requested_fact_signal,
        evidence_count=len(pack.messages),
    )


def parse_claim_extraction(content: str, pack: EvidencePack) -> ClaimExtraction:
    payload = parse_json_object(content)
    raw_insufficient = payload.get("insufficient_evidence")
    if isinstance(raw_insufficient, str):
        lowered = raw_insufficient.strip().casefold()
        if lowered in {"true", "1", "yes"}:
            raw_insufficient = True
        elif lowered in {"false", "0", "no"}:
            raw_insufficient = False
    if not isinstance(raw_insufficient, bool):
        raise ModelOutputError("insufficient_evidence must be boolean")

    raw_claims = payload.get("claims", [])
    if isinstance(raw_claims, dict):
        raw_claims = [raw_claims]
    if not isinstance(raw_claims, list):
        raise ModelOutputError("claims must be an array")
    if raw_insufficient:
        if raw_claims:
            raise CitationValidationError("insufficient output cannot contain claims")
        return ClaimExtraction(True, ())
    if not raw_claims:
        raise CitationValidationError("supported output requires claims")

    by_id = {item.message_id: item for item in pack.messages if item.message_id is not None}
    candidates: list[ClaimCandidate] = []
    for raw in raw_claims:
        if not isinstance(raw, dict):
            raise ModelOutputError("claim must be an object")
        kind = str(raw.get("kind", "finding")).strip().casefold()
        if kind not in _ALLOWED_KINDS:
            raise CitationValidationError("unsupported claim kind")
        text = str(raw.get("text", "")).strip()
        if not text:
            raise CitationValidationError("claim text is required")
        supports_raw = raw.get("supports")
        if not isinstance(supports_raw, list) or not supports_raw:
            raise CitationValidationError("every claim requires support")

        supports: list[ClaimSupport] = []
        seen_supports: set[tuple[int, str]] = set()
        for support_raw in supports_raw:
            if not isinstance(support_raw, dict):
                raise ModelOutputError("support must be an object")
            try:
                message_id = int(support_raw.get("message_id"))
            except (TypeError, ValueError) as exc:
                raise CitationValidationError("support message_id is invalid") from exc
            evidence = by_id.get(message_id)
            if evidence is None:
                raise CitationValidationError("support points outside admitted evidence")
            supplied_ref = str(support_raw.get("source_ref", "")).strip()
            if supplied_ref and supplied_ref != evidence.source_ref:
                raise CitationValidationError("support source_ref does not match message_id")
            quote = str(support_raw.get("quote", "")).strip()
            if not quote or quote not in (evidence.text or ""):
                raise CitationValidationError("support quote is not an exact substring of cited message")
            key = (message_id, quote)
            if key in seen_supports:
                continue
            seen_supports.add(key)
            supports.append(ClaimSupport(message_id=message_id, source_ref=evidence.source_ref, quote=quote))

        claim = GroundedClaim(kind=kind, text=text, supports=tuple(supports))
        quotes = tuple(item.quote for item in supports)
        risks = classify_claim_risk(text, quotes)
        _validate_literal_invariants(text, quotes)
        exact_preserved = all(normalize_text(quote) in normalize_text(text) for quote in quotes)
        introduced = _introduced_substantive_tokens(text, quotes)
        needs_semantic = bool(introduced) or not exact_preserved
        candidates.append(ClaimCandidate(claim=claim, risk_flags=risks, needs_semantic_verification=needs_semantic))
    return ClaimExtraction(False, tuple(candidates))


def classify_claim_risk(text: str, quotes: Sequence[str]) -> tuple[str, ...]:
    joined = " ".join((text, *quotes))
    risks: list[str] = []
    if _NUMBER_RE.search(joined):
        risks.append("number_age_dose")
    if _LATIN_OR_MODEL_RE.search(joined):
        risks.append("brand_model")
    if any(marker in normalize_text(joined).casefold() for marker in _COMPARISON_MARKERS):
        risks.append("comparison")
    if _NEGATION_RE.search(joined):
        risks.append("negation")
    if _CLINICAL_RE.search(joined):
        risks.append("clinical_action")
    return tuple(risks)


def parse_verifier_output(content: str, expected_indices: Sequence[int]) -> tuple[VerificationVerdict, ...]:
    payload = parse_json_object(content)
    values = payload.get("verdicts")
    if not isinstance(values, list):
        raise ModelOutputError("verdicts must be an array")
    expected = tuple(int(index) for index in expected_indices)
    verdicts: dict[int, VerificationVerdict] = {}
    for raw in values:
        if not isinstance(raw, dict):
            raise ModelOutputError("verdict must be an object")
        try:
            index = int(raw.get("claim_index"))
        except (TypeError, ValueError) as exc:
            raise ModelOutputError("claim_index is invalid") from exc
        entailed = raw.get("entailed")
        risk_ok = raw.get("risk_ok")
        if not isinstance(entailed, bool) or not isinstance(risk_ok, bool):
            raise ModelOutputError("verdict booleans are required")
        if index not in expected or index in verdicts:
            raise CitationValidationError("verifier returned unexpected or duplicate claim index")
        verdicts[index] = VerificationVerdict(index, entailed, risk_ok)
    if set(verdicts) != set(expected):
        raise ModelOutputError("verifier omitted claim indices")
    return tuple(verdicts[index] for index in expected)


def select_verified_claims(
    candidates: Sequence[ClaimCandidate],
    verdicts: Sequence[VerificationVerdict] = (),
) -> tuple[GroundedClaim, ...]:
    verdict_by_index = {item.claim_index: item for item in verdicts}
    verified: list[GroundedClaim] = []
    for index, candidate in enumerate(candidates):
        if not candidate.needs_semantic_verification:
            verified.append(candidate.claim)
            continue
        verdict = verdict_by_index.get(index)
        if verdict is None or not verdict.entailed:
            continue
        if candidate.risk_flags and not verdict.risk_ok:
            continue
        verified.append(candidate.claim)
    return tuple(verified)


def semantic_candidates(candidates: Sequence[ClaimCandidate]) -> tuple[tuple[int, ClaimCandidate], ...]:
    return tuple((index, candidate) for index, candidate in enumerate(candidates) if candidate.needs_semantic_verification)


def compose_verified_answer(claims: Sequence[GroundedClaim], pack: EvidencePack, *, question: str) -> AnswerResult:
    claims = tuple(claims)
    if not claims:
        raise CitationValidationError("no verified claims available for composition")
    answer_claims = tuple(item for item in claims if item.kind == "answer")
    finding_claims = tuple(item for item in claims if item.kind == "finding")
    disagreement_claims = tuple(item for item in claims if item.kind == "disagreement")
    conclusion_claims = tuple(item for item in claims if item.kind == "conclusion")
    direct = answer_claims[0].text if answer_claims else claims[0].text
    findings = tuple(item.text for item in finding_claims)
    disagreements = tuple(item.text for item in disagreement_claims)
    conclusion = conclusion_claims[0].text if conclusion_claims else None

    supports = [support for claim in claims for support in claim.supports]
    cited_ids = tuple(dict.fromkeys(support.message_id for support in supports))
    source_refs = tuple(dict.fromkeys(support.source_ref for support in supports))
    authors = {
        item.author for item in pack.messages
        if item.author and item.message_id in set(cited_ids)
    }
    confidence, confidence_reason = _confidence(len(cited_ids), len(authors), bool(disagreements))
    return AnswerResult(
        direct_answer=direct,
        key_findings=findings,
        disagreements=disagreements,
        practical_conclusion=conclusion,
        confidence=confidence,
        confidence_reason=confidence_reason,
        cited_message_ids=cited_ids,
        source_refs=source_refs,
        evidence_used_count=len(cited_ids),
        independent_authors_count=len(authors),
        insufficient_evidence=False,
        safety_note_if_needed=_archive_safety_note(question, claims),
        grounded_claims=claims,
    )


def _concept_terms(concepts: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for concept in concepts:
        normalized = normalize_text(str(concept)).casefold().strip()
        if not normalized:
            continue
        values = (normalized, *[token for token in _TOKEN_RE.findall(normalized) if len(token) >= 3])
        for value in values:
            if value and value not in seen:
                seen.add(value)
                out.append(value)
    return tuple(out[:24])


def _aspect_is_covered(aspect: str, *, topic_anchored: bool, hit_names: Sequence[str], family_names: Sequence[str]) -> bool:
    normalized = aspect.casefold().replace("-", "_")
    if normalized == "topic":
        return topic_anchored
    parts = {part for part in normalized.split("_") if len(part) >= 4 and part not in {"requested", "aspect"}}
    if not parts:
        return False
    for name in (*hit_names, *family_names):
        lowered = name.casefold().replace("-", "_")
        if any(part in lowered or lowered in part for part in parts):
            if not hit_names:
                return True
            if any(lowered == hit.casefold().replace("-", "_") for hit in hit_names):
                return True
    return False


def _requested_fact_signal(required: Sequence[str], evidence: str, direct_hits: int) -> bool:
    joined = " ".join(required).casefold()
    if any(key in joined for key in ("timing", "age", "quantity", "dose", "number")):
        return bool(_NUMBER_RE.search(evidence) or any(word in evidence for word in _NUMBER_WORDS))
    if "comparison" in joined:
        return any(marker in evidence for marker in _COMPARISON_MARKERS)
    if any(key in joined for key in ("recommend", "treatment", "method", "cause")):
        return direct_hits > 0
    return direct_hits > 0


def _validate_literal_invariants(claim_text: str, quotes: Sequence[str]) -> None:
    support_text = " \n ".join(quotes)
    support_norm = normalize_text(support_text).casefold()
    claim_norm = normalize_text(claim_text).casefold()

    for token in _NUMBER_RE.findall(claim_text):
        if token and token not in support_text:
            raise CitationValidationError("numeric fact is absent from cited support")
    for token in _LATIN_OR_MODEL_RE.findall(claim_text):
        if token and token.casefold() not in support_text.casefold():
            raise CitationValidationError("brand/model token is absent from cited support")

    claim_neg = bool(_NEGATION_RE.search(claim_norm))
    support_neg = bool(_NEGATION_RE.search(support_norm))
    if claim_neg != support_neg:
        raise CitationValidationError("negation polarity differs from cited support")

    if _comparison_is_reversed(claim_norm, support_norm):
        raise CitationValidationError("comparison direction is reversed")


def _comparison_is_reversed(claim: str, support: str) -> bool:
    for marker in _COMPARISON_MARKERS:
        if marker not in support or marker not in claim:
            continue
        s_left, s_right = support.split(marker, 1)
        c_left, c_right = claim.split(marker, 1)
        s_left_token = _edge_token(s_left, last=True)
        s_right_token = _edge_token(s_right, last=False)
        c_left_token = _edge_token(c_left, last=True)
        c_right_token = _edge_token(c_right, last=False)
        if s_left_token and s_right_token and c_left_token and c_right_token:
            if s_left_token == c_right_token and s_right_token == c_left_token:
                return True
    return False


def _edge_token(text: str, *, last: bool) -> str:
    tokens = [token.casefold() for token in _TOKEN_RE.findall(text) if token.casefold() not in _FRAMING]
    if not tokens:
        return ""
    return tokens[-1] if last else tokens[0]


def _introduced_substantive_tokens(claim: str, quotes: Sequence[str]) -> tuple[str, ...]:
    quote_tokens = {token.casefold() for token in _TOKEN_RE.findall(" ".join(quotes))}
    introduced: list[str] = []
    for token in _TOKEN_RE.findall(claim):
        lowered = token.casefold()
        if len(lowered) <= 1 or lowered in quote_tokens or lowered in _FRAMING:
            continue
        introduced.append(lowered)
    return tuple(dict.fromkeys(introduced))


def _confidence(evidence_count: int, author_count: int, disagreement: bool) -> tuple[str, str]:
    if evidence_count >= 4 and author_count >= 3 and not disagreement:
        return "high", f"پوشش آرشیوی قوی است: {evidence_count} پیام از {author_count} نویسنده مستقل."
    if evidence_count >= 2 or author_count >= 2:
        suffix = " همراه با اختلاف‌نظر ثبت‌شده." if disagreement else "."
        return "medium", f"پوشش آرشیوی متوسط است: {evidence_count} پیام از {author_count} نویسنده{suffix}"
    return "low", f"پوشش آرشیوی محدود است: {evidence_count} پیام از {author_count} نویسنده."


def _archive_safety_note(question: str, claims: Sequence[GroundedClaim]) -> str | None:
    combined = " ".join((question, *(claim.text for claim in claims)))
    if not _CLINICAL_RE.search(combined):
        return None
    return "این جمع‌بندی فقط بازتاب پیام‌های گروه است و برای تصمیم درمانی بیمار جایگزین ارزیابی حرفه‌ای نیست."
