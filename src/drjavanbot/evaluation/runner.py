"""CLI helpers: the retrieval gate and a one-off end-to-end question."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from drjavanbot.brain.retriever import DiscussionRetriever
from .archive_eval import evaluate, load_cases

# Minimum share of real questions whose answering discussion must be among
# the candidates the LLM reranker reads (top 40), and in the top 10.
GATE_RECALL_AT_40 = 0.85
GATE_RECALL_AT_10 = 0.65


def run_archive_eval(db_path: Path) -> tuple[dict[str, Any], bool]:
    retriever = DiscussionRetriever(db_path)
    connection = sqlite3.connect(db_path)
    try:
        cases = load_cases(connection)
    finally:
        connection.close()
    report = evaluate(lambda question: retriever.search([question], limit=100), cases)
    present = [case for case in report.cases if not case.case_id.startswith("absent")]
    recall_40 = round(sum(1 for c in present if c.rank and c.rank <= 40) / max(1, len(present)), 4)
    summary = {
        "cases": len(present), "recall_at_1": report.recall_at_1, "recall_at_5": report.recall_at_5,
        "recall_at_10": report.recall_at_10, "recall_at_40": recall_40, "mrr": report.mrr,
        "gates": {"recall_at_40": GATE_RECALL_AT_40, "recall_at_10": GATE_RECALL_AT_10},
    }
    passed = recall_40 >= GATE_RECALL_AT_40 and report.recall_at_10 >= GATE_RECALL_AT_10
    summary["passed"] = passed
    return {"summary": summary, "cases": [{"id": c.case_id, "rank": c.rank} for c in report.cases]}, passed


def ask_once(db_path: Path, question: str, *, secret_dir: Path):
    from drjavanbot.ai.config import AIConfig, AIConfigurationError
    from drjavanbot.brain.engine import ArchiveBrain
    from drjavanbot.brain.model import AvalAIJSONModel
    from drjavanbot.secrets import AVALAI_API_KEY_SECRET, LocalFileSecretStore

    api_key = LocalFileSecretStore(secret_dir).get_secret(AVALAI_API_KEY_SECRET)
    if not api_key:
        raise AIConfigurationError(f"no AvalAI key in {secret_dir}")
    from drjavanbot.knowledge.store import KnowledgeStore

    knowledge_path = db_path.parent / "knowledge.sqlite3"
    knowledge = KnowledgeStore(knowledge_path) if knowledge_path.exists() else None
    brain = ArchiveBrain(retriever=DiscussionRetriever(db_path, knowledge=knowledge),
                         model=AvalAIJSONModel(api_key=api_key, config=AIConfig.from_env()))
    return brain.answer(question).answer


def study_once(db_path: Path, knowledge_path: Path, *, limit: int | None, workers: int, secret_dir: Path) -> dict[str, Any]:
    from drjavanbot.ai.config import AIConfig, AIConfigurationError
    from drjavanbot.brain.model import AvalAIJSONModel
    from drjavanbot.knowledge.store import KnowledgeStore
    from drjavanbot.knowledge.study import study_archive
    from drjavanbot.secrets import AVALAI_API_KEY_SECRET, LocalFileSecretStore

    api_key = LocalFileSecretStore(secret_dir).get_secret(AVALAI_API_KEY_SECRET)
    if not api_key:
        raise AIConfigurationError(f"no AvalAI key in {secret_dir}")
    store = KnowledgeStore(knowledge_path)
    report = study_archive(db_path, store, AvalAIJSONModel(api_key=api_key, config=AIConfig.from_env()),
                           limit=limit, workers=workers,
                           progress=lambda done, total: print(f"\r{done}/{total}", end="", flush=True))
    print()
    return {"pending": report.pending, "studied": report.studied, "useful": report.useful,
            "failed": report.failed, "stopped_reason": report.stopped_reason, **store.stats()}


__all__ = ["GATE_RECALL_AT_10", "GATE_RECALL_AT_40", "ask_once", "run_archive_eval", "study_once"]
