# Intelligence v2 Source Policy

## Authority tiers

The source router chooses authority according to the user's requested fact, not a global one-source rule.

| Tier | Source | Appropriate authority |
|---|---|---|
| A | DrJavan Telegram Archive | What the group said, archive opinions/experience, terse archive topic lookups |
| B | Curated Dental Knowledge | Optional only; currently not populated because no provenance-backed KB has been approved |
| C | Scientific | PubMed/peer-reviewed literature, systematic reviews, guidelines and scientific dental facts |
| D | Current / Official | Current salary, prices, market, regulation, leadership and other time-sensitive claims |

Model memory, question wording, query terms and conversation context are **never** factual authorities.

## Routing rules

- `گروه درباره X چی گفته؟` -> Archive required.
- `فقط از گروه ...` -> Archive required and external factual sources are prohibited.
- `شایع‌ترین کیست ادنتوژنیک چیست؟` -> Scientific required; archive optional/supplementary.
- Current salary/cost/market -> Current Web required; Official optional unless the requested fact is regulatory/official.
- Regulation/license/law -> Official required; current web may corroborate but cannot replace official authority.
- Hybrid explicit archive + scientific/current -> each explicitly requested source is required.
- Clinical recommendation -> Scientific primary; archive may be shown separately as anecdotal discussion.
- Bare dental topic/acronym such as `RCT?` -> Archive fast path; adding a factual/recommendation facet re-routes to the corresponding authority.

## Scientific source quality

PubMed items carry publication type and methodological strength. Clinical guideline/systematic review evidence is weighted above single primary papers when topic/requested-fact relevance is comparable. Publication age affects recent/guideline questions but does not automatically invalidate older landmark evidence for evergreen facts.

## Current-source admission

A discovery result is not evidence until the original page is fetched and admitted. Current evidence must satisfy:

- requested geography or a defensible project default;
- current/recent date signal appropriate to the facet;
- topical dental/professional context;
- source-quality threshold;
- for salary/cost, a monetary amount/range with currency/unit near the requested fact.

A year, percentage or unrelated numeric token alone is not salary/cost evidence. Search-engine snippets or spam domains cannot authorize a claim.

## Freshness

- Archive: freshness is normally not a validity requirement unless the user explicitly asks about historical timing.
- Evergreen scientific: no forced current window; relevance and methodology dominate.
- Recent guideline: recent scientific/official evidence is required.
- Current salary/cost/market: current evidence window is required and the answer names the data year/date.
- Realtime/breaking: shortest cache/freshness window.

## Attribution

UI and claim metadata must distinguish Archive, Scientific, Official and Current. Telegram opinion is never relabeled scientific truth. A current estimate is never laundered into a timeless fixed fact. Conflicts between independent sources must be preserved rather than averaged away silently.
