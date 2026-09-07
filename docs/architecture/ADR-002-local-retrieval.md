# ADR-002 — Local parser, SQLite index and retrieval implementation

Status: Accepted — Stage 2

## Context

The repository contains 247 Telegram Desktop HTML export pages. The bot must search the whole archive without sending the archive to an AI model and without requiring Telegram or AvalAI credentials during ingestion/retrieval.

## Decisions

### Parsing

- `TelegramHTMLParser` parses one `messages*.html` page at a time with BeautifulSoup's tolerant HTML parser. The entire archive is never loaded into RAM at once.
- A page must contain a Telegram `history` container, at least one direct message block, unique DOM ids and a closing `</html>` marker. Truncated pages fail with `ParseError`.
- `message_id` and raw `dom_id` remain distinct. Joined Telegram messages inherit authors deterministically; cross-page inheritance is resolved by the indexer.
- Raw text preserves visible line breaks and inline link labels. Search normalization is stored separately.
- Same-page/cross-page replies, forward origin/date, hyperlinks and media metadata retain source identity.

### Normalization

Retrieval normalization unifies Arabic/Persian yeh/kaf, Persian/Arabic/Latin digits, ZWNJ/whitespace, common alef variants, Arabic diacritics and English case. Unicode punctuation becomes token boundaries. `text_raw` is never replaced by normalized text.

### SQLite / FTS5

- Schema version: `2`, with explicit v1→v2 migration.
- Canonical rows live in `messages`; FTS5 is an external-content index synchronized by SQLite triggers.
- `fts5vocab` supplies a bounded vocabulary for fuzzy correction; fuzzy matching never scans the raw archive.
- Full reindex builds a temporary database, validates SQLite/foreign keys/FTS, checkpoints WAL and atomically swaps the active DB.
- Incremental indexing hashes each export page, pre-parses every changed/dependent page before mutation, and publishes each changed page in one transaction. Unchanged files are skipped.
- Runtime DB/WAL/SHM/cache remain ignored by Git.

### Retrieval

Signals are combined locally:

1. exact normalized phrase;
2. normalized primary-token AND match;
3. FTS5/BM25 across primary tokens and phrase-level lexical expansions;
4. bounded fuzzy expansion from FTS vocabulary;
5. version-controlled Persian/English dental lexicon;
6. optional author/date filters;
7. reply-chain and adaptive neighboring context;
8. same-author exact duplicate collapse while retaining identical claims from independent authors;
9. deterministic local score and auditable source locator.

Candidate and evidence caps are enforced in code. Long substantive replies receive the reply target plus small local context; short responses such as «آره» receive more context because they are not useful as standalone evidence.

## Consequences

- Normal questions require no AI call for retrieval.
- Stage 3 can consume structured `EvidenceCandidate` objects and never needs to parse HTML directly.
- Fuzzy search quality depends on indexed vocabulary and deliberately favors precision over aggressive correction.
- BeautifulSoup materializes one HTML page in memory; this is bounded by Telegram's page size rather than total archive size. If future exports contain extremely large single pages, a streaming parser can replace the implementation behind the same parser contract.
