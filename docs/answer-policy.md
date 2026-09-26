# Executable answer policy

The active design is the archive brain: [`ai-pipeline.md`](ai-pipeline.md). The multi-source Intelligence v2 contract below is superseded and kept in [`history/`](history/) for reference.

## Authority

The Telegram archive is the only source. Answers report what the group said, attributed and dated (old prices, regulations and products are flagged by year). Model memory is never factual evidence; a question the group never discussed gets "not discussed".

## Grounding

Every factual claim must have one or more validated supports. The model selects opaque support IDs only; application code owns the `evidence_id`, source metadata and verbatim quote, and validates them against the supplied evidence pool. Required source, requested facet and freshness are validated before synthesis. Unknown citations, quote mismatch, truncated structured output and unsupported current amounts fail closed.

## Insufficient evidence

Insufficient evidence is a valid answer. The application must not fill missing archive, scientific, official or current evidence from model knowledge.

## Presentation

Archive answers are labelled `🗂 جمع‌بندی آرشیو گروه`; scientific answers `📚 پاسخ علمی مستند`; current answers `🌐 اطلاعات به‌روز`; hybrid answers separate the source classes. Direct answer comes first.

## Legacy rollback

When Intelligence v2 feature flags are disabled, the pre-v2 archive-only runtime remains available as a compatibility rollback. Its historical archive-only rules are preserved in repository history and `docs/original-analysis-policy.md`; they do not describe the active Stage 2 multi-source authority model.
