# ADR-007 — AI-planned semantic retrieval over the local archive

## Status

Accepted.

## Context

The previous answer path searched the raw user question first and invoked AI query expansion only when a lexical retrieval heuristic considered the result weak. This allowed superficially strong keyword hits to bypass semantic planning. Questions such as «کدوم برند کامپوزیت خوبه؟» could therefore retrieve literal mentions without adequate product/experience coverage, while low-information words such as «چرا» could influence unrelated searches.

The archive must remain the only factual source. We also need bounded cost/latency, auditable local retrieval, reply/context support, strict citation validation, and no new heavy external vector dependency.

## Decision

Use the following bounded pipeline:

1. normalize and reject empty/punctuation-only input;
2. obtain a compact AI Search Plan (or an index-bound cached plan);
3. suppress deterministic low-information words from lexical queries;
4. run several independent query families through the existing SQLite/FTS5/exact/fuzzy search stack;
5. fuse scores deterministically with cross-family coverage and soft source/thread diversity;
6. use bounded reply/context expansion;
7. if coverage remains weak, make exactly one AI refinement call using vocabulary observed in retrieved context and the current canonical index;
8. build the bounded evidence pack;
9. use the final AI call to select relevant evidence and synthesize the answer;
10. validate every citation against the supplied evidence pack.

Search-plan and refinement strings are never evidence. Product-like corpus vocabulary can only guide another local lookup. Facts become answerable only after an actual archive message is retrieved and admitted to the evidence pack.

## Call budget

The application-level ceiling is three logical AI calls:

- normal uncached: planner + synthesis = 2;
- weak retrieval: planner + refinement + synthesis = 3;
- normal malformed synthesis: planner + synthesis + repair = 3;
- weak + malformed synthesis: fail closed after call 3; no call 4.

Answer-cache hits use zero AI calls. A plan-cache hit can reduce an answer-cache miss to one synthesis call.

## Corpus-awareness

No per-question persistent dynamic index and no hand-maintained brand dictionary are introduced. Weak-path vocabulary hints are derived at request time from the current canonical FTS index and the context of retrieved candidates. The index fingerprint is part of planner cache identity, so index changes invalidate plan reuse.

## Why not an external vector database now

The current failure was primarily query understanding and coverage, not lack of storage capacity. AI planning plus multi-family local retrieval addresses that failure while keeping operations, cost, privacy and deployment complexity low. A vector/embedding service may be reconsidered only after measured archive regressions show recall gaps that this architecture cannot address.

## Safety invariants

- raw `گروه دکتر جوان/messages*.html` files are never modified by search/runtime code;
- model memory cannot be cited as archive evidence;
- unknown brand/product names are not hard-coded;
- strict citation validation is retained;
- `insufficient_evidence` remains a correct outcome;
- Telegram owner/access/security/updater contracts are unchanged.
