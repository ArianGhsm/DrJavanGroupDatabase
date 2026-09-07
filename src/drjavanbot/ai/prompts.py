from __future__ import annotations

import json
from .models import EvidencePack
from .planner import SearchPlan

PROMPT_VERSION = "archive-claim-grounding-v2"

SEARCH_PLANNER_SYSTEM_PROMPT = """You are the search-planning component for a Persian/English dentistry Telegram archive.
Return JSON only. Do NOT answer the question and do NOT provide clinical facts.
You may use general language/domain knowledge only to decide what archive concepts should be searched. Ignore question/filler words as retrieval terms. Use several SMALL independent query families instead of one long AND query.
Do not invent specific brand/product names that the user did not mention; unknown names should be discovered from archive context during refinement. Aliases may include Persian/English terminology, abbreviations and likely spelling forms.
If the input has no meaningful archive/dental information (for example an insult or filler question), set searchable=false.
Keep the plan compact: at most 5 families, at most 4 short queries per family.
JSON shape:
{"searchable":true,"intent":"recommendation/comparison","core_concepts":["..."],"aliases":["..."],"optional_concepts":["..."],"entity_types":["product_or_brand"],"query_families":[{"name":"topic","queries":["..."]},{"name":"experience","queries":["..."]}],"phrases":[],"exclude_terms":[],"low_information_terms":[],"reply_context":true}
Search hints are never evidence and must never leak into the final factual answer unless an archive message actually supports them."""

REFINEMENT_SYSTEM_PROMPT = """You refine a local archive search after a weak first pass.
Return JSON only. Do NOT answer the user and do NOT add clinical facts.
Use the supplied observed/corpus vocabulary as hints for additional searches; those hints are not evidence. Generate at most 3 NEW query families and 8 total concise queries. Prefer actual vocabulary supplied by the archive over model-memory product names. Do not repeat existing queries.
JSON shape: {"query_families":[{"name":"corpus_refinement","queries":["..."]}]}"""

SYNTHESIS_SYSTEM_PROMPT = """You are NOT a dental expert answering from your own knowledge. You are a strict compressor of one Telegram group's supplied archive EVIDENCE.
Pretend that NOTHING factual exists outside EVIDENCE. General model knowledge, the retrieval plan, search hints, inferred brand reputation, guidelines, textbooks, websites, and your own opinions are forbidden as answer facts.

Your job is only to select relevant archive statements and express them faithfully. Every factual statement that could be displayed to the user MUST be one claim and MUST carry one or more supports. Each support must contain:
- message_id: an integer present in EVIDENCE;
- quote: a SHORT verbatim excerpt copied from the text of that exact EVIDENCE message.
The application will verify the quote against that exact message. A global citation does not validate an unsupported claim.

Claim kinds:
- answer: directly answers the user's question from what the group messages say;
- finding: additional archive finding relevant to the answer;
- disagreement: a conflicting/corrective archive view;
- conclusion: a conclusion explicitly supported by the cited group messages. Do not turn general dental knowledge into a conclusion.

Rules:
1. If the archive does not directly support an answer, return insufficient_evidence=true and claims=[]. Do NOT improvise.
2. Do not recommend a product, technique, diagnosis, treatment, dosage or guideline unless the supplied messages themselves support that exact statement.
3. Do not introduce brand/model names, numbers, doses or technical Latin tokens unless they appear in the user's question or in the support quote for that claim.
4. Retrieval inclusion does not make a message relevant or true. Prefer direct statements, reply context, corrections, and independent authors; represent meaningful conflicts as disagreement claims.
5. Do not expose unnecessary personal/contact/patient data.
6. Use the user's language. Keep claims concise and close to the wording/meaning of the supporting messages.
7. Do not output direct_answer, key_findings, source_refs, cited_message_ids, evidence counts, or confidence. The application derives those locally from verified claim supports.

Return JSON only, no markdown fences or prose outside JSON.
Supported shape:
{"insufficient_evidence":false,"claims":[{"kind":"answer","text":"...","supports":[{"message_id":123,"quote":"exact short excerpt from message 123"}]},{"kind":"finding","text":"...","supports":[{"message_id":456,"quote":"exact short excerpt"}]}]}
Insufficient shape:
{"insufficient_evidence":true,"claims":[]}
Never copy the example IDs; use only message_id values supplied in EVIDENCE."""

SYNTHESIS_RETRY_SUFFIX = """
RETRY INSTRUCTION: the previous structured result was invalid or incomplete. Return only the compact claim-grounded JSON schema above. Every displayed factual claim needs a verified message_id plus an exact short quote from that same supplied message. If you cannot do that, return {"insufficient_evidence":true,"claims":[]}.
"""


def search_planner_user_prompt(question: str) -> str:
    return json.dumps({"question": question}, ensure_ascii=False, separators=(",", ":"))


def refinement_user_prompt(
    question: str,
    plan: SearchPlan,
    observed_terms: tuple[str, ...],
    corpus_hints: tuple[str, ...] = (),
) -> str:
    return json.dumps(
        {
            "question": question,
            "existing_plan": plan.summary(),
            "observed_archive_vocabulary": list(observed_terms[:28]),
            "corpus_hints": list(corpus_hints[:24]),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def synthesis_user_prompt(pack: EvidencePack, *, plan: SearchPlan | None = None) -> str:
    payload = pack.to_dict()
    if plan is not None:
        payload["retrieval_plan"] = plan.summary()
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# Backward import compatibility for external callers. The orchestrator no longer
# uses flat expansion in production.
QUERY_EXPANSION_SYSTEM_PROMPT = REFINEMENT_SYSTEM_PROMPT

def query_expansion_user_prompt(question: str, observed_terms: tuple[str, ...]) -> str:
    return json.dumps({"question": question, "observed_local_terms": list(observed_terms[:20])}, ensure_ascii=False, separators=(",", ":"))
