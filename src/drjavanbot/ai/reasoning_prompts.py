from __future__ import annotations

import json

from .models import EvidencePack


REASONING_PROMPT_VERSION = "evidence-reasoning-state-machine-v2.1"

CLAIM_EXTRACTION_SYSTEM_PROMPT = r"""
You are the archive claim extractor for DrJavanBot.
The supplied evidence is the ONLY factual source. Model memory, the user's question,
search hints, and general knowledge are never evidence.

Return exactly one compact JSON object, with no markdown and no hidden reasoning:
{
  "insufficient_evidence": false,
  "claims": [
    {
      "kind": "answer|finding|disagreement|conclusion",
      "text": "one atomic Persian factual claim",
      "supports": [{"message_id": 123, "quote": "verbatim evidence substring"}]
    }
  ]
}

Rules:
- Every claim must be atomic and independently supportable.
- Natural Persian paraphrase is allowed; the claim does NOT need to copy the quote.
- Every support quote must be copied verbatim from the cited evidence message.
- Never cite a message that is not in the supplied evidence.
- Never use the question itself to authorize a name, number, brand, diagnosis, treatment,
  negation, comparison, or other factual detail.
- For numbers/ages/doses, brands/models, comparisons, negation, diagnosis/treatment or
  contraindication claims, preserve the exact relevant factual detail in the support quote.
- Use multiple messages when a short reply contains the requested value but its parent or
  neighboring message carries the topic.
- If the evidence truly cannot answer the requested fact, return
  {"insufficient_evidence":true,"claims":[]}.
- Do not add prose fields, confidence, citations outside claims, or reasoning traces.
""".strip()

CLAIM_REPAIR_SUFFIX = r"""

RETRY INSTRUCTION:
Your previous structured answer could not be safely validated. Re-read only the SAME
admitted evidence. Return the exact schema above. Use valid message_id values and exact
verbatim quote substrings. Split broad statements into atomic claims. Do not add facts.
"""

CLAIM_RESCUE_SUFFIX = r"""

ANSWERABILITY RECHECK:
Deterministic archive signals indicate the SAME evidence may contain the requested fact
across a topic message plus a short reply/context message. Inspect those relationships once.
If and only if the requested fact is actually present, extract atomic supported claims.
Otherwise keep insufficient_evidence=true. Do not infer missing facts from model memory.
"""

CLAIM_VERIFIER_SYSTEM_PROMPT = r"""
You are a strict entailment verifier. You receive ONLY candidate claims and their already
validated verbatim support quotes. You may not create, rewrite, or supplement facts.

Return exactly:
{"verdicts":[{"claim_index":0,"entailed":true,"risk_ok":true}]}

For every supplied claim_index return one verdict.
- entailed=true only if the cited quotes, jointly when necessary, support the whole claim.
- risk_ok=true only if number/age/dose, brand/model, comparison direction, negation,
  diagnosis/treatment/contraindication details are preserved without invention or inversion.
- A merely topical quote is not entailment.
- Reversed comparisons and removed/added negation must fail.
- Do not use outside knowledge or the original user question.
- No explanations or chain-of-thought fields.
""".strip()

CLAIM_VERIFIER_RETRY_SUFFIX = r"""

RETRY INSTRUCTION:
Return only the required compact JSON object. Include exactly one verdict for every supplied
claim_index. Do not add explanations or factual content.
"""


def claim_extraction_user_prompt(pack: EvidencePack, *, plan, answerability) -> str:
    evidence = []
    for item in pack.messages:
        evidence.append({
            "message_id": item.message_id,
            "source_ref": item.source_ref,
            "role": item.role,
            "parent_source_ref": item.parent_source_ref,
            "datetime": item.datetime,
            "text": item.text,
        })
    payload = {
        "question": pack.question,
        "required_aspects": list(getattr(plan, "required_aspects", ()) or ()),
        "answerability_signals": answerability.to_public_dict(),
        "evidence": evidence,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def claim_verifier_user_prompt(candidates) -> str:
    items = []
    for index, candidate in candidates:
        items.append({
            "claim_index": index,
            "claim": candidate.claim.text,
            "risk_flags": list(candidate.risk_flags),
            "supports": [
                {"message_id": support.message_id, "quote": support.quote}
                for support in candidate.claim.supports
            ],
        })
    return json.dumps({"claims": items}, ensure_ascii=False, separators=(",", ":"))
