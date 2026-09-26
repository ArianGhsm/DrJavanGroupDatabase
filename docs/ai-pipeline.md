# The archive brain

Goal: behave like a model that has studied every message of Dr Mehdi Javan's group. Any question gets an answer synthesised from what the group actually said, with every point traceable to the original messages. Facts come only from the archive; the LLM understands, selects and writes, but its own knowledge is never a source.

## Knowledge unit: the discussion

`storage/discussions.py` rebuilds conversations from Telegram's own structure on every index run:

- a message's parent is the message it replies to, or — for a joined continuation — the previous message by the same author;
- a message with no parent, or whose parent is more than 24h older, opens a new discussion;
- discussions over 40 messages are chunked; later chunks carry the opening question as a header.

Keys are Telegram message ids (`<root>` for the first chunk, `<root>.2+` for later ones), stable across reindexes. Full archive: ~58k discussions.

## Answering (`brain/engine.py`)

```text
question + last 3 turns of this user's conversation
  → UNDERSTAND (fast model): standalone question + 3-5 queries in archive vocabulary
  → RETRIEVE   (local, brain/retriever.py): 40 candidate discussions
  → RERANK     (fast model): which candidates actually answer it (0-3 relevance)
  → ANSWER     (owner-selected model): JSON answer from ≤6 discussions
```

Grounding: every statement (headline, points, Dr. Javan's view, disagreements, takeaway) must carry `{"id", "quote"}` supports whose quote is copied verbatim from the cited message and shares content with the statement. Statements failing this are dropped; an unsupported headline is replaced by the strongest verified point; nothing verified → "not discussed". The rendered "supporting phrases" are these verified quotes.

Privacy: phone numbers, e-mails and Telegram invite links are masked (`brain/redaction.py`) before any archive text is sent to the model — in answers, reranking and study.

- Small talk is answered after UNDERSTAND without searching.
- Answers are cached by the *resolved* standalone question plus index fingerprint and model/pipeline signature, so a follow-up such as «مقصر کیه؟» never reuses another topic's answer.
- 3 AI calls per question (2 when nothing relevant is found). Understand/rerank use `deepseek-v4-flash` with thinking disabled.

## Retrieval

`DiscussionRetriever` scores discussions by IDF-weighted coverage of the query over the whole discussion and (×0.5) its opening message, with FTS5 BM25 as tie-breaker, fused across all phrasings with reciprocal-rank fusion. Token equivalents: numbers (هفتم/7/۷), colloquial vowels (دندان/دندون) and single-word lexicon synonyms. A `DenseIndex` (embeddings) can join the same fusion; it is not enabled yet.

## Studying the archive (`knowledge/`)

A one-time, resumable pass writes a card for every discussion with ≥3 messages and ≥2 participants (~23k): topic, main question, summary, verified points, Dr. Javan's view and 8-20 search keywords in other vocabularies (English terms, lay/technical words, spellings). Cards are stored in `knowledge.sqlite3`, separate from the rebuildable index, keyed by discussion key + content hash, so only new or changed discussions are studied again.

Cards join retrieval as another ranked list (their keywords bridge paraphrases such as «لق» ↔ «راکینگ») and give the reranker each candidate's topic and summary. Cards are never cited; answers still quote messages.

Start it from the owner panel (📚 آرشیو و ایندکس → 🧠 مطالعهٔ آرشیو) or `drjavanbot study [--limit N] [--workers 6]`.

## User feedback

Every answer carries 👍/👎 (only the asker can rate). 👎 asks for a reason: wrong / was in the group but not found / incomplete / off-topic. The owner panel (🧰 ابزارها → 🗳 بازخورد کاربران) shows counts and the latest 👎 with question, answer and reason; `drjavanbot feedback` exports them as JSON for prompt/retrieval tuning. Unrated answers are kept 90 days.

## Evaluation

`src/drjavanbot/evaluation/archive_questions.json` holds real questions from the archive re-worded as a user would ask them, each tied to the discussion that answers it (resolved at runtime by a verbatim anchor phrase), plus questions about things never discussed.

```bash
drjavanbot --db runtime/data/archive.sqlite3 archive-eval --strict   # retrieval gate (no AI)
drjavanbot --db runtime/data/archive.sqlite3 ask "سؤال"              # one full answer with the configured key
```

Current retrieval (34 questions): R@1 0.26, R@5 0.65, R@10 0.74, R@40 0.91. CI fails if R@40 < 0.85 or R@10 < 0.65. The reranker only needs the target within the 40 candidates.

For comparison, the replaced rule-based planner pipeline scored R@10 0.29 on the same questions.

## Cache and version

`ANSWER_PIPELINE_VERSION` (`ai/config.py`) and `BRAIN_VERSION` (`brain/engine.py`) are part of cache identity; bump them when retrieval or answering semantics change.
