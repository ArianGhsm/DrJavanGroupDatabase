# Executable answer policy

This document defines the product invariant for DrJavanBot answers.

## Single factual authority

The Telegram archive is the **only factual authority** for user-visible answers.

The model's general dental/medical/product knowledge, the wording of the user's question, search-plan concepts, aliases, refinement hints, inferred brand reputation, textbooks, guidelines and web knowledge are **not evidence**. They may help plan or broaden retrieval, but no fact from them may appear in an answer unless a retrieved archive message independently supplies that fact.

If the archive does not supply enough evidence, the application returns a deterministic archive-not-found/insufficient response. It must not fill the gap from model knowledge.

## Claim-level grounding contract

A supported synthesis is structured as claims. Every claim has one or more archive supports. Every support must provide:

- a `message_id` present in the supplied evidence pack;
- a short quote copied from that exact message.

Validation is fail-closed and local:

1. the cited message must actually have been supplied to synthesis;
2. the quote must literally exist in that exact evidence message after normalization;
3. every support quote must appear intact and in the same word order inside the displayed claim;
4. technical/product/number tokens may come only from verified support quotes, never merely from the user's question;
5. every remaining substantive claim word must also occur in the verified support quotes;
6. global citations cannot authorize unrelated prose.

The exact-sequence rule is intentional. It prevents transformations such as `A بهتر از B` → `B بهتر از A`, or `خوب نیست` → `خوب`, even though those variants reuse much of the same vocabulary. The model may add only neutral framing around intact archive wording.

`cited_message_ids`, `source_refs`, evidence counts, independent-author counts and displayed archive-coverage confidence are computed by the application from verified supports. Model-provided values for those fields are not authoritative.

## Retrieval before synthesis

Search may use general language/domain knowledge to build aliases and query families. This is a retrieval aid only. Important hits are expanded with reply/nearby context before synthesis. A short context-dependent message such as “نه خوب نبود” is not valid standalone evidence when its referent is unknown.

Weak retrieval may use a bounded refinement pass based on observed archive vocabulary. Refinement output is also not evidence. The global logical AI-call cap remains bounded.

## Evidence quality

Retrieval inclusion does not make a message relevant or true. Synthesis should prefer direct statements, reply context, later corrections and independent authors, and should preserve meaningful disagreement instead of manufacturing consensus. Duplicate or quoted copies must not inflate independent-support counts.

## Insufficient evidence

`insufficient_evidence=true` must contain no factual claims. The user-visible text is generated locally and states that sufficient evidence was not found in the group. Any model prose accompanying an insufficient result is ignored.

## User-visible transparency

The Telegram answer is presented as **«جمع‌بندی پیام‌های گروه»**, not as an expert/model opinion. Verified support quotes and message IDs are surfaced directly in the answer UI, with a separate source browser for the underlying messages.

The footer explicitly states that the archive is the source of the answer and AI is used only for search, evidence selection and arranging validated archive wording.

## Confidence

Only `high`, `medium`, or `low` archive-coverage labels are used. They are derived locally from verified evidence/independent-author counts and disagreement. They are not a model estimate of scientific truth and are not a clinical confidence percentage.

## People and privacy

Do not diagnose personality, mental health, morality or inherent trustworthiness. Avoid repeating phone numbers, addresses, patient-identifying or other unnecessary sensitive information. Evidence packing already redacts obvious PII before synthesis; visible support quotes come from the validated/redacted evidence representation.

## Medical limitation

The bot reports what the archived group messages say. It does not convert group consensus into a clinical guideline and does not append an independent AI recommendation. For clinically consequential topics, the application adds a deterministic note that the output is only an archive summary, not a guideline or independent AI advice.

## Cache/versioning invariant

Grounding-contract changes require a `PROMPT_VERSION` bump. Answer-cache keys include that version, so responses created under a weaker synthesis contract cannot be reused after a grounding-policy upgrade.
