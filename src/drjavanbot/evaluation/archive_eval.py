"""Discussion-level retrieval evaluation on real archive questions.

Each case is a question a user would ask, written in different words from the
archive, whose answer is known to live in one discussion. A retriever is any
callable ``question -> ordered discussion ids``; metrics say how often the
target discussion is found and how high it ranks.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
import sqlite3
from typing import Callable, Iterable, Sequence

from drjavanbot.normalization import normalize_text

Retriever = Callable[[str], Sequence[int]]


@dataclass(frozen=True, slots=True)
class EvalCase:
    case_id: str
    question: str
    targets: frozenset[int]  # empty for absent cases


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    rank: int | None  # 1-based rank of the first target, None if missed
    returned: int


@dataclass(frozen=True, slots=True)
class EvalReport:
    cases: tuple[CaseResult, ...]
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    absent_empty_rate: float | None

    def summary(self) -> str:
        absent = "—" if self.absent_empty_rate is None else f"{self.absent_empty_rate:.2f}"
        return (f"R@1={self.recall_at_1:.2f} R@5={self.recall_at_5:.2f} R@10={self.recall_at_10:.2f} "
                f"MRR={self.mrr:.3f} absent_empty={absent} n={sum(1 for c in self.cases if not c.case_id.startswith('absent'))}")


def load_cases(connection: sqlite3.Connection) -> tuple[EvalCase, ...]:
    payload = json.loads(files("drjavanbot").joinpath("evaluation/archive_questions.json").read_text(encoding="utf-8"))
    out: list[EvalCase] = []
    for case in payload["cases"]:
        anchor = normalize_text(case["anchor"])
        rows = connection.execute(
            "SELECT DISTINCT dm.discussion_id FROM messages m JOIN discussion_messages dm ON dm.message_row_id=m.id "
            "WHERE m.text_normalized LIKE ?",
            (f"%{anchor}%",),
        ).fetchall()
        if not rows:
            raise ValueError(f"evaluation anchor not found in archive: {case['id']}")
        out.append(EvalCase(case["id"], case["question"], frozenset(int(row[0]) for row in rows)))
    for case in payload.get("absent", ()):
        out.append(EvalCase(case["id"], case["question"], frozenset()))
    return tuple(out)


def evaluate(retriever: Retriever, cases: Iterable[EvalCase], *, absent_threshold: Callable[[str], bool] | None = None) -> EvalReport:
    """Run ``retriever`` on every case.

    ``absent_threshold`` optionally decides, per absent question, whether the
    system correctly reported "not discussed" (True = correct).
    """
    results: list[CaseResult] = []
    present_ranks: list[int | None] = []
    absent_ok: list[bool] = []
    for case in cases:
        ranked = list(dict.fromkeys(retriever(case.question)))
        if not case.targets:
            results.append(CaseResult(case.case_id, None, len(ranked)))
            absent_ok.append(absent_threshold(case.question) if absent_threshold else not ranked)
            continue
        rank = next((index for index, value in enumerate(ranked, start=1) if value in case.targets), None)
        present_ranks.append(rank)
        results.append(CaseResult(case.case_id, rank, len(ranked)))
    total = max(1, len(present_ranks))

    def recall(k: int) -> float:
        return round(sum(1 for rank in present_ranks if rank is not None and rank <= k) / total, 4)

    return EvalReport(
        cases=tuple(results),
        recall_at_1=recall(1),
        recall_at_5=recall(5),
        recall_at_10=recall(10),
        mrr=round(sum(1.0 / rank for rank in present_ranks if rank) / total, 4),
        absent_empty_rate=round(sum(absent_ok) / len(absent_ok), 4) if absent_ok else None,
    )


def discussions_for_messages(connection: sqlite3.Connection, message_row_ids: Sequence[int]) -> list[int]:
    """Map a ranked list of message row ids to ranked, de-duplicated discussion ids."""
    if not message_row_ids:
        return []
    placeholders = ",".join("?" for _ in message_row_ids)
    mapping = dict(connection.execute(
        f"SELECT message_row_id, discussion_id FROM discussion_messages WHERE message_row_id IN ({placeholders})",
        tuple(message_row_ids),
    ).fetchall())
    return list(dict.fromkeys(mapping[row] for row in message_row_ids if row in mapping))


__all__ = ["CaseResult", "EvalCase", "EvalReport", "discussions_for_messages", "evaluate", "load_cases"]
