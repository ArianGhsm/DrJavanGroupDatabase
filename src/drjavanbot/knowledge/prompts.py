STUDY_PROMPT = """You study one discussion from a Persian Telegram group of dentists run by Dr. Mehdi Javan (مهدی جوان) and write an index card so it can be found and summarised later. Use ONLY the messages given.

Each message has an id, author, date, the id it replies to, and text.

Return JSON:
{"useful": true | false,
 "topic": "<short Persian topic title, e.g. «راکینگ روکش زیرکونیوم»>",
 "question": "<the main question or case discussed, in one Persian sentence>",
 "summary": "<2-4 Persian sentences: what was asked and what the group concluded>",
 "answers": [{"text": "<one point made in the discussion>", "sources": [<message ids>]}],
 "javan_view": {"text": "<what Dr. Javan said>", "sources": [<ids>]} | null,
 "keywords": ["<8-20 search terms>"]}

Rules:
- useful=false for chit-chat, greetings, jokes, announcements with no professional content, or unanswered one-liners; then other fields may be empty.
- Cite only ids from the given messages. Attribute opinions; do not add knowledge that is not in the messages.
- keywords: the terms someone might use to look for this discussion — Persian formal and colloquial spellings, English dental terms and abbreviations, product/brand names, the technical term for lay descriptions and the lay words for technical terms.
"""
