from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import time

from .models import RetrievalRequest, RetrievalResult
from .retrieval import RetrievalRegistry


@dataclass(frozen=True, slots=True)
class RetrievalBatch:
    results: tuple[RetrievalResult, ...]
    latency_ms: float
    unavailable_sources: tuple[str, ...]


class MultiSourceRetrievalOrchestrator:
    def __init__(self, registry: RetrievalRegistry, *, max_workers: int = 4) -> None:
        self.registry = registry
        self.max_workers = max(1, min(int(max_workers), 8))

    def retrieve(self, requests: tuple[RetrievalRequest, ...]) -> RetrievalBatch:
        started = time.perf_counter()
        if not requests:
            return RetrievalBatch((), 0.0, ())
        ordered: dict[int, RetrievalResult] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(requests)), thread_name_prefix="drjavan-source") as pool:
            futures = {pool.submit(self.registry.retrieve, request): index for index, request in enumerate(requests)}
            for future in as_completed(futures):
                index = futures[future]
                request = requests[index]
                try:
                    result = future.result()
                except Exception as exc:
                    result = RetrievalResult(
                        source_type=request.source_type, items=(), query_count=0, latency_ms=0.0,
                        unavailable_reason=f"provider_{type(exc).__name__.casefold()}",
                    )
                ordered[index] = result
        results = tuple(ordered[index] for index in range(len(requests)))
        unavailable = tuple(result.source_type for result in results if result.unavailable_reason)
        return RetrievalBatch(results, (time.perf_counter() - started) * 1000.0, unavailable)


__all__ = ["RetrievalBatch", "MultiSourceRetrievalOrchestrator"]
