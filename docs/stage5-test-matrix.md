# Stage 5 verification matrix

Status values: **PASS** = actually executed in the Stage-5 runtime; **PREVIOUS PASS** = executed in the earlier development stage but not rerun as a complete current-repo suite in Stage 5; **STAGE 6** = requires the real checkout/server/network/credentials and must be reported by Codex.

| Area | Verification | Status |
|---|---|---|
| Configuration | numeric owner/config contracts | PREVIOUS PASS |
| Parser | Telegram default/service/reply/forward/media/malformed fixtures | PREVIOUS PASS |
| Normalization | Persian/Arabic/Latin normalization | PREVIOUS PASS |
| Search | exact/FTS5/BM25/fuzzy/synonym/filter retrieval | PREVIOUS PASS |
| Context | reply chain/adaptive neighboring context/dedupe | PREVIOUS PASS |
| Reindex | full idempotency, failed-build preservation, incremental changes | PREVIOUS PASS |
| AI evidence | one-call path, weak-retrieval expansion, evidence caps | PREVIOUS PASS |
| Citation validation | fabricated message/source rejection | PREVIOUS PASS |
| Provider failures | mock 401/429/timeout/invalid JSON/retry | PREVIOUS PASS |
| SecretStore | atomic replace, permissions/redaction behavior | PREVIOUS PASS |
| Telegram owner UX | owner/private checks, settings, key flow, sources, access modes | PASS |
| Update durability | leased claim/release/complete across restart | PASS |
| Polling acknowledgement | failed update prevents offset advancing past failure | PASS |
| Rate limit concurrency | 12 concurrent attempts at limit 3 accept exactly 3 | PASS |
| Callback hardening | malformed access/rate callback does not crash or mutate state | PASS |
| Telegram formatting | invalid HTML send falls back to plain text | PASS |
| Python syntax | `python -m compileall -q src` | PASS |
| systemd unit structure | `systemd-analyze verify` with expected mock paths present | PASS |
| DeepSeek V4 payload test | asserts `thinking={type: disabled}` and no `reasoning_effort` | STAGE 6 (test committed; full Stage-3 module checkout unavailable locally) |
| Complete current test suite | `pytest -q` from real repository checkout | STAGE 6 |
| Full 247-page index | real `drjavanbot reindex` with actual counts/timing | STAGE 6 |
| Full-corpus retrieval benchmark | actual DB size/query latency on deployment host | STAGE 6 |
| SQLite FTS5 on server | `drjavanbot-smoke` | STAGE 6 |
| Telegram network/token | `drjavanbot-smoke --telegram` / `getMe` | STAGE 6 |
| AvalAI real key/network | owner `/settings` → Test AvalAI after deployment | STAGE 6 |
| systemd live runtime | start/status/journal/graceful stop/restart | STAGE 6 |

Stage-5 locally executed Telegram + hardening subset: **25 tests passed**. Stage-1/2/3 test suites passed during their own development stages, but they are deliberately not represented as a fresh Stage-5 full-suite run.

No fabricated full-archive timing, real-network result, or live-service success is recorded here.
