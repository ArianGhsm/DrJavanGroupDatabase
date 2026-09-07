# Stage-1 data audit

## Repository inventory

At the audited `main` commit, the repository contains the existing analysis-policy README and the Telegram Desktop export directory `گروه دکتر جوان`.

The message archive is contiguous and contains **247 HTML message pages**: `messages.html`, then `messages2.html` through `messages247.html`. Tree metadata shows typical pages around 0.8–1.3 MB, with the final page smaller (~0.57 MB). The source working set is therefore hundreds of megabytes even though the Git repository is compressed. The indexer must be one-time/incremental; raw HTML must not be rescanned per user question.

Export support assets include CSS/JS, contact vCards, image/UI assets, stickers and a video sticker. These assets are not primary searchable evidence, but parser logic must retain media/link metadata referenced by a message without reading unrelated binary payloads into memory.

## Inspected HTML shapes

Samples from the first and last archive pages confirm standard Telegram Desktop HTML:

- Content message: `div.message.default...` with id such as `message302010`.
- Service/date blocks: `div.message.service` with their own DOM ids.
- Timestamp: `div.date.details` with a full `title`, e.g. a local timestamp plus `UTC+03:30`.
- Author: `div.from_name`.
- Text: `div.text`, including nested anchors and `<br>` line breaks.
- Replies: `div.reply_to.details`.
- Same-page reply links use `#go_to_message<ID>` plus `GoToMessage(ID)`.
- Cross-page replies use paths like `messages246.html#go_to_message301977`.
- Text can contain Telegram mentions and ordinary external links.
- Reactions are represented separately and must not be concatenated into message text.
- Export assets show media categories such as photo, file, voice, video, contact, location, music and calls; Stage 2 must probe the message-side HTML variants and store only metadata/path/label needed for search and citation.

## Ordering

File ordering is numeric, not lexicographic: `messages.html` is page 1, `messages2.html` is page 2, ..., `messages247.html` is page 247. `source_order` is the order of a message block within a page. A global ordering can be derived from `(source_page, source_order)` and validated against timestamps.

## Parser invariants for Stage 2

1. Never mutate archive files.
2. Preserve `text_raw`; normalization is a second field.
3. Preserve exact DOM id even when no numeric Telegram message id can be derived.
4. Parse full timestamp from `title`; do not infer dates from visible `HH:MM`.
5. Preserve reply target and cross-page source.
6. Preserve anchors/links and media metadata separately.
7. Do not treat reactions as independent textual evidence.
8. Service messages are stored but excluded from ordinary content retrieval by default.
9. A malformed page fails closed for that page; do not silently publish a partial replacement into the active index.
10. Preserve a source hash and parser/ingest version so incremental indexing is reproducible.

## Known audit limitation

Stage 1 inspected the complete Git tree and representative HTML from the start/end of the export, including real cross-page and same-page reply structures. It did not exhaustively parse every one of the 247 pages; exhaustive structural counts and exact message/media/forward statistics are intentionally a Stage-2 parser/index output.
