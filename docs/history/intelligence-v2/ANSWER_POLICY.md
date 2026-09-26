# Intelligence v2 Answer Policy

## Core invariant

Every user-visible factual claim must be supported by retrieved evidence from the source tier authorized for that claim. Unsupported model knowledge is prohibited.

## Claim contract

The model emits only compact claim text plus opaque `support_ids`. Application code then constructs each grounded claim with its semantic kind, `SourceSupport`, known `evidence_id`, source type/ref and an exact verbatim excerpt from the evidence pool. The model cannot author quote text or citation metadata.

The direct answer is the first grounded claim, not ungrounded framing added before citations.

## Validation

Validation is local and fail-closed:

1. evidence ID must be known;
2. application-owned support span must be a contiguous verbatim excerpt of the referenced evidence;
3. required source types must be represented;
4. requested facet must be directly supported;
5. current claims must pass freshness;
6. salary/cost direct answers must contain a supported numeric amount/range, currency/unit and data year/date;
7. every numeric token in a claim must occur in its validated support; local numeric-span attachment is allowed only inside the same EvidenceItem and with matching support context;
8. structured output truncated by token limits is invalid;
9. failed repair never falls back to model memory.

`insufficient_evidence=true` is a valid outcome and must contain no invented factual completion.

## Answer modes

- Archive: `🗂 جمع‌بندی آرشیو گروه`
- Scientific: `📚 پاسخ علمی مستند`
- Current: `🌐 اطلاعات به‌روز`
- Hybrid: direct answer followed by clearly separated scientific/current/archive sections as evidence allows.

The direct answer comes first. Empty decorative sections are omitted.

## Archive claims

Archive answers describe what archived participants said. Frequency of messages is not scientific truth. Archive citations retain message/source references and contextual/reply evidence. When the user says `فقط از گروه`, external facts are excluded.

## Scientific and clinical claims

Clinical/factual dental claims require scientific/official support appropriate to the requested facet. Archive material may be shown as a separately labelled anecdotal discussion. Conflicting scientific evidence is preserved. The bot does not claim a Telegram consensus is a guideline.

## Current claims

Current answers are estimates/observations tied to date, geography and employment/market model. Salary responses prefer ranges and distinguish fixed salary, clinic/percentage, public/contract or own-practice models only when evidence explicitly supports those distinctions.

## Privacy

Conversation context stores bounded semantic interpretation state, not raw prior questions, prompts, archive text or model responses. Visible archive evidence remains subject to existing PII/privacy protections.

## Confidence

Only qualitative `high/medium/low` confidence is used. It describes coverage/authority/consistency of retrieved evidence, not a fabricated probability or clinical diagnosis certainty.
