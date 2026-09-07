# ADR-001 — Core architecture for DrJavanBot

**Status:** Accepted for implementation  
**Stage:** 1/5  
**Owner:** Arian Ghasempour

## Context

The repository is primarily a Telegram Desktop HTML export. The archive contains 247 message pages (`messages.html`, then `messages2.html` through `messages247.html`). Individual pages are roughly 0.8–1.3 MB in the inspected tree, so repeatedly scanning HTML or sending the archive to an LLM per question is neither efficient nor safe.

The export preserves stable HTML message anchors, timestamps, authors, text and reply links. Later pages contain both same-page replies and cross-page links such as `messages246.html#go_to_message301977`. This makes a local evidence graph practical.

## Decision

Use Python 3.11+, SQLite and FTS5 as the local retrieval core.

### Runtime query path

Telegram question → deterministic normalization → local query variants / dental lexicon → exact + lexical + FTS5/BM25 retrieval → bounded fuzzy matching → reply/context expansion → duplicate/near-duplicate collapse → local scoring → compact evidence pack → optional AI query-assist only when retrieval signal is weak → AvalAI/DeepSeek evidence filtering + synthesis → citation validation → Telegram response.

The normal path is designed for one AI call. A second AI call is a fallback, not the default.

### Index path

Telegram HTML export → parser → loss-minimizing `MessageRecord` → normalization → SQLite canonical rows + link/media tables → external-content FTS5 index.

## Storage and atomicity

`archive_files` is the ingestion manifest keyed by source path and SHA-256.

Full rebuild: build and validate a sibling temporary database, ingest all sources, run integrity checks, then atomically replace the active DB on the same filesystem. The previous known-good DB remains valid until replacement succeeds.

Incremental rebuild: hash files; skip unchanged sources; fully parse a changed source before opening the write transaction; then delete/replace that source's rows, update FTS and manifest in one transaction. Failure rolls back the entire source update.

A text/content hash may mark duplicate/copy clusters, but identical text posted by different people/times must not be discarded as evidence.

## Message identity

Telegram HTML has non-content/service anchors as well as numeric message anchors. Therefore `dom_id` preserves the exact HTML id; `message_id` is nullable and only contains a safely parsed numeric id; `(source_file, dom_id)` is the canonical uniqueness key; and `source_locator` preserves an auditable pointer such as `گروه دکتر جوان/messages247.html#message302010`.

Cross-page reply references store both target numeric id and source file when present.

## Search strategy

Stage 2 must compose, not replace, these signals: exact phrase, normalized lexical match, FTS5/BM25, bounded typo/fuzzy score, Persian/English dental synonyms and abbreviations, optional author/date filters, reply and nearby-discussion context, duplicate collapse, local relevance score and bounded evidence selection.

Fuzzy matching must be candidate-bounded; it must never compare every query against every raw archive message at request time.

## Evidence and answer policy

The LLM is not a general dentistry oracle in this product. It may understand the question, help expand a difficult query, judge relevance and synthesize retrieved evidence. It must not fill an archive evidence gap from its own knowledge.

Confidence is `high`, `medium` or `low`, with reasons based on evidence count, independent authors, consistency, context quality and contradictions. No invented percentage confidence is allowed. Frequency is a signal, not truth.

## Security and privacy

- Owner authorization will use only numeric `TELEGRAM_OWNER_ID`.
- No secrets are committed.
- AvalAI API key is intentionally absent from `.env.example`; later stages implement a runtime `SecretStore` controlled by the owner in a private Telegram chat.
- Logs are structured for secret redaction.
- Patient/personal data not needed to answer a question should not be echoed.
- Archive discussions are not presented as guidelines or clinical authority.

## Dependency choice

Stage 1 declares only BeautifulSoup as a runtime parser dependency. SQLite/FTS5, logging, dataclasses and configuration use the Python standard library. Telegram and HTTP client dependencies are deferred until their stages so the dependency surface is not guessed early.
