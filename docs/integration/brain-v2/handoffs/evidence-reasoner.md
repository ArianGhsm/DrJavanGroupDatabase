# PARALLEL HANDOFF — Evidence Reasoner v2

## Branch / baseline

- Repository: `ArianGhsm/DrJavanGroupDatabase`
- Branch: `parallel/brain-evidence-reasoner-v2`
- Locked BASE_SHA: `4226f2c30a04d89a28afe8284be5c3e5f657a2a6`
- Validated implementation HEAD before this documentation-only handoff commit: `96b7b0ec89588b66ca20fc6f6b516abf7f589e62`
- Final branch tip: the commit containing this handoff file; resolve the branch ref at delivery. (A Git commit cannot truthfully contain its own SHA because the SHA depends on the file contents and commit metadata.)
- No merge or rebase from later `main` or parallel branches was performed.
- No merge to `main` was performed.

## State machine

```text
CACHE
  | miss
  v
PLAN
  v
RETRIEVE ---- weak/faceted ----> REFINE (optional, one bounded call)
  |                                |
  +--------------------------------+
  v
PACK (discussion-first, relation-aware, hard-capped)
  v
ANSWERABILITY TRIAGE
  v
EXTRACT ATOMIC CLAIMS
  v
DETERMINISTIC SUPPORT VALIDATION
  | exact/extractive claim                    | semantic paraphrase/high-risk ambiguity
  |                                           v
  |                                  SEMANTIC ENTAILMENT VERIFY (conditional)
  |                                           |
  +-------------------------------------------+
  v
LOCAL COMPOSE FROM VERIFIED CLAIMS ONLY
```

Failure transitions are explicit and telemetry-visible: `no_candidates`, `topic_found_facet_missing`, `fragmented_but_answerable`, `conflicting_only`, `validation_failed`, `structured_output_failed`, `provider_failed`, `budget_exhausted`, `true_insufficient`.

Model-level `insufficient_evidence=true` is no longer authoritative. When bounded archive signals indicate the same evidence pack is facet-rich/fragmented but plausibly answerable, one rescue extraction is allowed; otherwise the system remains fail-closed.

## Contracts / schemas changed

### Atomic claim extraction

Extraction returns only a compact structured object:

```json
{
  "insufficient_evidence": false,
  "claims": [
    {
      "kind": "answer|finding|disagreement|conclusion",
      "text": "atomic Persian claim",
      "supports": [{"message_id": 123, "quote": "verbatim evidence substring"}]
    }
  ]
}
```

Every support is deterministically bound to an admitted evidence `message_id`; supplied quotes must be exact substrings of that same message. The question, model memory, planner terms and corpus hints are never accepted as factual support.

### Semantic verifier

Only claims that require semantic entailment checking are sent to the verifier. It sees only the candidate claim, risk flags and its already validated exact support quotes. It does not receive the original question and cannot add facts.

```json
{"verdicts":[{"claim_index":0,"entailed":true,"risk_ok":true}]}
```

Malformed verifier output has at most one targeted repair if global call budget remains.

### Answerability

`AnswerabilityAssessment` exposes bounded, inspectable signals only: required-facet coverage, topic anchoring, directness, discussion coherence, independent authors, correction/conflict signal, requested-fact signal and evidence count. It contains no raw private reasoning.

### Telemetry

Metadata-only AI telemetry adds: `stage`, `result_class`, `reason_code`, `logical_call`, `evidence_count`. Existing token usage, model and latency fields remain. Prompts, raw evidence, responses, secrets and chain-of-thought are not persisted.

## Evidence packing changes

`build_evidence_pack` now:

- represents top discussions before allowing repeated hits from one local thread;
- prioritizes direct reply parents and correction-like context;
- distributes context round-robin across selected anchors;
- preserves short context-dependent replies;
- suppresses same-author duplicate text while retaining identical statements from independent authors as corroboration;
- preserves phone/email/invite-link redaction;
- keeps existing hard message/token caps.

Retrieval ranking itself was not changed.

## High-risk claim policy

The following claim classes receive stricter deterministic and/or semantic verification:

- numbers, ages and doses;
- brand/model/technical literals;
- comparison/superiority direction;
- negation polarity;
- diagnosis/treatment/contraindication or other clinical-action language.

Fail-closed guarantees include:

- citation to a message outside admitted evidence is rejected;
- quote not present verbatim in the cited message is rejected;
- source/message mismatch is rejected;
- invented number/age/dose is rejected before semantic verification;
- invented brand/model literal is rejected when absent from support;
- negation removal/addition is rejected;
- reversed comparison direction is rejected;
- topical citation alone does not authorize a paraphrased claim; semantic entailment must pass;
- model memory or the user question cannot become factual support;
- a paraphrased claim cannot surface when its required verifier call cannot fit inside the global budget.

Natural Persian paraphrase remains possible: paraphrases are accepted only after claim-to-support entailment verification.

## Prompt / validation / cache versions

- Reasoning prompt version: `evidence-reasoning-state-machine-v2.1`
- Validation semantics version: `claim-support-validation-v2.2`
- Existing planner/retrieval prompt contracts remain in use.
- Response cache key now includes both reasoning prompt version and validation-semantics version in addition to existing prompt/model/config/index signatures.
- Provider, structured-output and validation failures are not cached.
- Successful grounded answers remain cacheable; cache hit costs zero logical AI calls.

## AI call-budget policy

Hard global ceiling: **4 logical AI calls** per uncached request. Provider-internal HTTP retries remain separately bounded by `AIConfig`; they are transport retries, not semantic loops.

Typical paths:

| Path | Logical calls |
|---|---:|
| Cache hit | 0 |
| Simple/extractive | planner + extraction = 2 |
| Simple semantic paraphrase | planner + extraction + verifier = 3 |
| Weak/faceted, extractive | planner + refinement + extraction = 3 |
| Weak/faceted + conditional verifier/repair/rescue | up to 4 |
| Fifth/sixth semantic loop | forbidden |

Stage output budgets remain bounded:

- planner: `max(config.planner_max_output_tokens, 480)`;
- refinement: `max(config.refinement_max_output_tokens, 360)`;
- extraction: question-class `budget.max_output_tokens`;
- extraction repair/rescue: bounded `_repair_tokens(...)`;
- verifier: bounded approximately `320..800` tokens;
- verifier targeted repair: bounded approximately `480..900` tokens.

If the four-call ceiling is consumed before a required semantic verifier, unverifiable paraphrased claims are not displayed. The user may retry rather than the system silently making a fifth call.

## Files changed

Implementation/test files relative to the locked base:

- `src/drjavanbot/ai/orchestrator.py`
- `src/drjavanbot/ai/evidence.py`
- `src/drjavanbot/ai/reasoning.py` (new)
- `src/drjavanbot/ai/reasoning_prompts.py` (new)
- `src/drjavanbot/ai/telemetry.py`
- `tests/test_brain_evidence_reasoner_v2.py` (new)
- `tests/test_stage9_semantic_retrieval.py` (provider-failure contract aligned with controlled response policy)
- `PARALLEL_HANDOFF.md` (this file)

No `ai/planner.py`, `ai/retrieval.py`, `search/**`, index internals, runtime/security/updater code, or archive HTML was modified.

## Test / benchmark results

CI validation of implementation HEAD `96b7b0ec89588b66ca20fc6f6b516abf7f589e62` against the exact locked base passed all workflow stages:

- compile: PASS
- SQLite FTS5 verification: PASS
- environment shell compatibility: PASS
- full pytest: **186 passed in 9.10s**
- full-archive strict semantic benchmark: PASS, `strict_failures=[]`
- systemd syntax/fixed-path verification: PASS

Full-archive benchmark:

- archive files: **247**
- messages: **249,907**
- DB size: **373,555,200 bytes**
- strict top-k: **12**
- median retrieval latency: **2768.293 ms**
- index build: **278.8318 s**
- all benchmark gates passed, including composite recommendation, pediatric orthodontic timing, e.max direct/Persian queries, short RCT, absent-noise and no-evidence sentinel cases.
- Pediatric orthodontic timing retrieved both required anchor groups (`2/2`) with complete colocation.

The CI-only draft PR used to trigger the repository workflow tests the branch changes against the same exact locked base. Its synthetic merge ref exists only inside GitHub Actions; it was never merged into this branch or `main`.

## Behavioral deltas

### False-insufficient

Before this branch, model `insufficient_evidence` could terminate synthesis even when retrieval contained fragmented but usable evidence. The new answerability layer can trigger exactly one bounded rescue over the same admitted pack when topic/facet/context signals support it. A short answer reply plus a topic-bearing parent is explicitly preserved and test-covered.

### Validation brittleness

The state machine no longer requires every substantive Persian token in final prose to be copied word-for-word from a quote. Instead, exact support identity/quote integrity is deterministic, and meaningful paraphrase is separately verified for entailment. Harmless archive framing can remain cheap/extractive; true semantic rewrites require verifier approval.

### Provider / structured failures

Timeout/rate-limit/provider failures return a controlled retry message and `provider_failed` telemetry; they are not interpreted as proof that the archive lacks evidence. Empty/malformed model content is classified as structured-output failure. These failure answers are not cached.

## Risks / unresolved items

- Legacy `validate_answer_payload` remains for compatibility/tests, but the new orchestrator does not rely on its brittle all-substantive-token vocabulary rule.
- Answerability is deliberately a bounded heuristic layer, not a source of facts; every displayed factual claim still requires support validation and, where needed, entailment verification.
- Under the strict four-call budget, a heavily faceted request that already used planning/refinement/repair may fail closed before an optional semantic verifier rather than exceed the ceiling.
- Provider-failure `AnswerResult` retains `insufficient_evidence=True` for current UI/schema compatibility, but its user-visible text and telemetry explicitly classify `provider_failed` and state that archive insufficiency was not established.
- Same-text independent-author statements are retained as corroboration; same-author duplicate text is suppressed.

## Integration dependencies / conflict surface

- Current `SearchPlan` and `RetrievalReport` contracts are consumed without changing planner or retrieval ranking.
- Parallel planner/retrieval branches may later evolve those contracts; integration should adapt the boundary rather than importing retrieval-ranking changes into this branch.
- Main likely conflict surface: `src/drjavanbot/ai/orchestrator.py`, `evidence.py`, telemetry schema and any future prompt/cache-version edits.
- No owner/security/updater/runtime changes are required by this branch.

## Archive immutability

`گروه دکتر جوان/messages*.html` and all raw archive/runtime DB/secrets/sessions/logs/backups were left untouched. The archive remains factual source-of-truth evidence only.