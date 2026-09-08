from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from typing import Iterable

from .models import EvidenceItem


DEFAULT_MULTILINGUAL_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"


@dataclass(frozen=True, slots=True)
class DenseCapability:
    available: bool
    model_name: str
    reason: str


def dense_capability(model_name: str = DEFAULT_MULTILINGUAL_EMBEDDING_MODEL) -> DenseCapability:
    if importlib.util.find_spec("sentence_transformers") is None:
        return DenseCapability(False, model_name, "sentence_transformers_not_installed")
    return DenseCapability(True, model_name, "runtime_available_model_may_require_local_cache")


class ExperimentalDenseReranker:
    """Optional local/private-friendly dense reranker; never auto-downloads a model.

    Stage 1 keeps this experimental. Construction requires the caller to provide
    a locally cached SentenceTransformer model, preventing accidental archive
    upload to an external embedding API or network download in production.
    """

    def __init__(self, model) -> None:
        self.model = model

    def rerank(self, query: str, items: Iterable[EvidenceItem]) -> tuple[EvidenceItem, ...]:
        values = tuple(items)
        if not values:
            return ()
        texts = [f"passage: {item.text}" for item in values]
        vectors = self.model.encode([f"query: {query}", *texts], normalize_embeddings=True)
        q = vectors[0]
        scored = []
        for item, vector in zip(values, vectors[1:]):
            score = float(q @ vector)
            scored.append((score, item))
        scored.sort(key=lambda pair: -pair[0])
        return tuple(item for _score, item in scored)


__all__ = ["DEFAULT_MULTILINGUAL_EMBEDDING_MODEL", "DenseCapability", "dense_capability", "ExperimentalDenseReranker"]
