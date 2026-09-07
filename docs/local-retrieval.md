# Local indexing and retrieval

Stage 2 works without a Telegram token and without an AvalAI API key.

## Commands

From the repository root after installation:

```bash
# Incremental index; performs a full build automatically if the DB does not exist.
python -m drjavanbot index

# Force a full atomic rebuild.
python -m drjavanbot reindex

# Local retrieval.
python -m drjavanbot search "RCT"
python -m drjavanbot search "e max" --author "مهدی جوان" --limit 10

# Metadata and integrity.
python -m drjavanbot stats
python -m drjavanbot health

# Isolated benchmark: builds a temporary DB and does not replace runtime data.
python -m drjavanbot benchmark "RCT" "ایمپلنت" "e max"
```

Default paths come from `.env.example` / `Settings`:

- archive: `گروه دکتر جوان`
- active DB: `runtime/data/archive.sqlite3`

`--archive-dir` and `--db` can override them for CLI utilities.

## Index behavior

`index` fingerprints every `messages*.html` page with SHA-256. Unchanged pages are skipped. A changed page is fully parsed before its previous rows are removed. If a changed page is malformed, the healthy indexed copy remains in place.

`reindex` builds and validates a separate temporary DB before an atomic filesystem replacement. The old database is not replaced if parsing or integrity checks fail.

The parser discovers every filename matching Telegram's `messages.html`, `messages2.html`, … pattern; it does not use a fixed list or sample pages.

## Retrieval output

Each `EvidenceCandidate` retains:

- canonical `MessageRecord`;
- `message_id`, source file and source locator;
- author and datetime;
- raw and normalized text;
- local score;
- matched terms and match reasons;
- reply/context messages with their own source locators;
- cluster key/size and duplicate metadata.

The source locator format is repository-relative, for example:

`گروه دکتر جوان/messages247.html#go_to_message302013`

Stage 3 must preserve this locator through evidence packing and citations.
