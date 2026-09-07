# Stage-2 implementation contract

Stage 2 must implement the parser, normalizer, SQLite/FTS5 index and local retrieval behind these interfaces without changing their semantics gratuitously.

## Parser

`TelegramExportParser.parse_file(ArchiveFile) -> Iterable[MessageRecord]`

Required behavior: deterministic; read-only source access; complete parse before publishing a changed file; raw + normalized text; message/service distinction; same-page and cross-page reply targets; links and media metadata; explicit `ParseError` on unsafe/incomplete parse.

Page number mapping: `messages.html -> 1`; `messagesN.html -> N`.

## Canonical record

`drjavanbot.domain.MessageRecord` is the loss-minimizing parser output. Stage 2 may add backward-compatible fields but must not remove source identity/citation fields. `dom_id` and `message_id` must remain distinct.

## Storage

Use `drjavanbot.storage.schema.schema_sql()` as schema contract, migrating deliberately if implementation evidence requires a schema change.

Full index: temporary DB → complete ingest → integrity check → atomic swap.

Incremental index: hash manifest → skip unchanged files → parse changed file fully → one DB transaction per changed-file publication → rollback on failure.

## Search

`SearchBackend.search(SearchQuery) -> Sequence[EvidenceCandidate]`; `get_message(message_id)`; `get_context(message, before, after, follow_reply)`; `stats()`.

`EvidenceCandidate` must retain original `MessageRecord`, local score, matched terms, match reasons, context and duplicate/cluster metadata.

Search is a bounded composition of exact, normalized, FTS/BM25, fuzzy and synonym signals. Fuzzy work must be applied only to a bounded candidate vocabulary/result set.

## Source integrity

Every candidate sent to later AI stages must retain an auditable `source_locator`. Stage 2 tests must demonstrate that a result can be traced back to the exact source file and message anchor.

## Expected Stage-2 commands

Implement a small CLI or internal command surface for at least full index/reindex, incremental index, local search, index stats and health/integrity. No AvalAI call and no Telegram token are needed in Stage 2.
