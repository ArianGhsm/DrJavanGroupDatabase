# Executable answer policy

The active product contract is Intelligence v2. See:

- [`intelligence-v2/SOURCE_POLICY.md`](intelligence-v2/SOURCE_POLICY.md)
- [`intelligence-v2/ANSWER_POLICY.md`](intelligence-v2/ANSWER_POLICY.md)
- [`intelligence-v2/ARCHITECTURE.md`](intelligence-v2/ARCHITECTURE.md)

## Authority

The Telegram archive is authoritative for **what the group said**. It is no longer the universal factual authority. Scientific dental facts require Scientific/Official evidence; current salary/price/market claims require dated Current/Official evidence; regulation requires Official evidence. Explicit `فقط از گروه` forbids external factual material. Model memory is never factual evidence.

## Grounding

Every factual claim must have one or more validated supports. The model selects opaque support IDs only; application code owns the `evidence_id`, source metadata and verbatim quote, and validates them against the supplied evidence pool. Required source, requested facet and freshness are validated before synthesis. Unknown citations, quote mismatch, truncated structured output and unsupported current amounts fail closed.

## Insufficient evidence

Insufficient evidence is a valid answer. The application must not fill missing archive, scientific, official or current evidence from model knowledge.

## Presentation

Archive answers are labelled `🗂 جمع‌بندی آرشیو گروه`; scientific answers `📚 پاسخ علمی مستند`; current answers `🌐 اطلاعات به‌روز`; hybrid answers separate the source classes. Direct answer comes first.

## Legacy rollback

When Intelligence v2 feature flags are disabled, the pre-v2 archive-only runtime remains available as a compatibility rollback. Its historical archive-only rules are preserved in repository history and `docs/original-analysis-policy.md`; they do not describe the active Stage 2 multi-source authority model.
