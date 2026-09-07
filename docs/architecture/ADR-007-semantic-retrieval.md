# ADR-007 — AI-planned semantic retrieval over the local archive

## Status

Accepted.

## Context

The previous answer path searched the raw user question first and invoked AI query expansion only when a lexical retrieval heuristic considered the result weak. This allowed superficially strong keyword hits to bypass semantic planning. Questions such as «کدوم برند کامپوزیت خوبه؟» could therefore retrieve literal mentions without adequate product/experience coverage, while low-information words such as «چرا» could influence unrelated searches.

A second archive-specific problem is conversational fragmentation. A material, technique or complication may be named in one message and evaluated, corrected or compared only in the next several messages or in a reply chain. Treating each lexical hit as a standalone document loses that discussion structure; naively sending wide neighborhoods for every hit would instead waste tokens and SQLite work.

The archive must remain the only factual source. We also need bounded cost/latency, auditable local retrieval, reply/context support, strict citation validation, and no new heavy external vector dependency.

## Decision

Use the following bounded pipeline:

1. normalize and reject empty/punctuation-only input;
2. obtain a compact AI Search Plan (or an index-bound cached plan);
3. suppress deterministic low-information words from lexical queries;
4. run several independent query families through the existing SQLite/FTS5/exact/fuzzy search stack with context disabled;
5. fuse scores deterministically with cross-family coverage and soft source/thread diversity;
6. open bounded discussion windows only around the strongest fused anchors, following reply ancestry and nearby chronological messages;
7. if coverage remains weak, make exactly one AI refinement call using vocabulary observed in retrieved context and the current canonical index;
8. build the bounded evidence pack with diverse primaries first and conversation context round-robin;
9. use the final AI call to select relevant evidence and synthesize the answer;
10. validate every displayed claim against an exact quote from an admitted archive message.

Search-plan and refinement strings are never evidence. Product-like corpus vocabulary can only guide another local lookup. Facts become answerable only after an actual archive message is retrieved and admitted to the evidence pack.

## Conversation-aware context without token explosion

Context expansion happens **after** multi-query fusion. Losing candidates from each search family never receive before/after hydration. At most a small bounded set of top fused anchors is expanded.

The window is adaptive and deterministic:

- several search hits within a short span of the same archive page are treated as a likely multi-message discussion and receive a wider but still bounded neighborhood;
- one nearby hit receives a medium neighborhood;
- short/context-dependent messages and replies receive extra local context because they are often meaningless alone;
- isolated self-contained messages receive only a small neighborhood.

The evidence pack keeps the existing hard message/token ceilings. Normal requests reserve roughly one quarter of message slots for context. When multiple top anchors are explicitly marked as discussion windows, up to roughly one third is reserved. Reply parents and direct reply relations are prioritized, then nearby/topically overlapping neighbors. Context is distributed round-robin across anchors so one long thread cannot consume the entire prompt. Any unused context reserve is returned to lower-ranked primary evidence.

This design adds **no AI call**. Conversation structure is recovered locally from SQLite after retrieval and remains subject to the same grounding validator as primary hits.

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

The current failure was primarily query understanding, coverage and discussion context, not lack of storage capacity. AI planning plus multi-family local retrieval and post-fusion discussion windows address those failures while keeping operations, cost, privacy and deployment complexity low. A vector/embedding service may be reconsidered only after measured archive regressions show recall gaps that this architecture cannot address.

## Safety invariants

- raw `گروه دکتر جوان/messages*.html` files are never modified by search/runtime code;
- model memory cannot be cited as archive evidence;
- Search Plan, user wording and observed vocabulary are retrieval aids, never factual support;
- unknown brand/product names are not hard-coded;
- strict exact-quote claim validation is retained for primary and context messages alike;
- `insufficient_evidence` remains a correct outcome;
- context expansion never exceeds the configured hard evidence-token/message budgets;
- Telegram owner/access/security/updater contracts are unchanged.
