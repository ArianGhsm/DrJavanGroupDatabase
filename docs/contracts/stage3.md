# Stage-3 input contract

Stage 3 must build AI relevance/evidence packing/synthesis on top of Stage 2. It must not bypass the local index by reading all HTML files per user question.

## Stable retrieval entry point

```python
from drjavanbot.search import SearchQuery, SQLiteSearchBackend

backend = SQLiteSearchBackend(db_path)
candidates = backend.search(SearchQuery(raw_query=user_question))
```

Primary interface:

`SQLiteSearchBackend.search(SearchQuery) -> tuple[EvidenceCandidate, ...]`

Supporting interfaces:

- `get_message(message_id) -> MessageRecord | None`
- `get_context(message, before=2, after=3, follow_reply=True) -> tuple[MessageRecord, ...]`
- `stats() -> dict`

## EvidenceCandidate fields Stage 3 may rely on

- `message: MessageRecord`
- `local_score: float`
- `matched_terms: tuple[str, ...]`
- `match_reasons: tuple[str, ...]`
- `context: tuple[MessageRecord, ...]`
- `cluster_key: str | None`
- `cluster_size: int`
- `duplicate_of: int | None`

## MessageRecord fields that must survive evidence packing

At minimum:

- `message_id`
- `source_file`
- `source_locator`
- `source_page` / `source_order`
- `datetime` and `datetime_raw`
- `author`
- `text_raw`
- `reply_to_message_id` / `reply_source_file`
- `forwarded_from` / `forwarded_datetime_raw`
- links/media when relevant

`text_normalized` is for retrieval and should not replace `text_raw` in user-facing citations.

## Stage-3 rules

1. Treat Stage-2 ranking as retrieval evidence, not truth or scientific validity.
2. Preserve independent authors even when their text belongs to the same content cluster.
3. Do not count short reply/context messages as independent evidence merely because they are present in `context`.
4. Keep the compact evidence pack bounded; do not serialize the whole SQLite result set to DeepSeek.
5. Preserve `source_locator` unchanged and validate every model citation against candidates/context before rendering.
6. If Stage 2 returns weak or empty evidence, Stage 3 must allow an explicit insufficient-evidence answer rather than filling the gap with model knowledge.
7. An optional second AI call may help query expansion only when local retrieval is weak; it must feed variants back into `SearchQuery.variants`, then run local retrieval again.

No AvalAI-specific code is required by this contract; Stage 3 may implement the provider adapter separately.
