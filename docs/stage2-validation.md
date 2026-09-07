# Stage-2 validation coverage

Automated tests cover:

- Persian/Arabic/Latin normalization;
- Telegram service/default/joined/forwarded/media/reply parsing;
- cross-file reply targets;
- malformed/truncated HTML fail-closed behavior;
- v1→v2 schema migration;
- full-reindex idempotency and failed-full-build preservation;
- incremental no-op and changed-page replacement;
- malformed incremental source preserving the healthy indexed copy;
- joined-author inheritance across file boundaries;
- exact phrase, token, FTS/BM25, fuzzy and synonym retrieval;
- Persian/English queries;
- author/date filtering;
- reply/adaptive context;
- same-author duplicate collapse with independent-author retention;
- source-locator integrity;
- stats/health and benchmark helpers;
- an integration fixture copied from the observed `messages247.html` NPG/E.max discussion structure.

Full-corpus performance must be measured on a runtime where the repository/archive is mounted locally. The GitHub connector used during Stage 2 exposes repository files for inspection/write but does not mount all 247 HTML files into the Python runtime; therefore no fabricated full-archive timing is recorded here.
