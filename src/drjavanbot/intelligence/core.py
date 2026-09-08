from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .answerability import assess_requested_fact_coverage
from .models import EvidenceItem, QuestionUnderstanding, RequestedFactCoverage, RetrievalRequest, SourceRoute
from .planning import QuestionIntelligenceEngine
from .query_generation import generate_retrieval_requests
from .retrieval import RetrievalRegistry
from .routing import route_sources


@dataclass(frozen=True, slots=True)
class IntelligencePlan:
    understanding: QuestionUnderstanding
    route: SourceRoute
    retrieval_requests: tuple[RetrievalRequest, ...]
    planner_fallback_used: bool
    model_decision: object | None = None


class DentalIntelligenceCore:
    def __init__(
        self,
        *,
        question_engine: QuestionIntelligenceEngine | None = None,
        registry: RetrievalRegistry | None = None,
    ) -> None:
        self.question_engine = question_engine or QuestionIntelligenceEngine()
        self.registry = registry or RetrievalRegistry()

    def plan(self, question: str) -> IntelligencePlan:
        understanding, decision, fallback = self.question_engine.understand(question)
        route = route_sources(understanding)
        requests = generate_retrieval_requests(understanding, route)
        return IntelligencePlan(
            understanding=understanding,
            route=route,
            retrieval_requests=requests,
            planner_fallback_used=fallback,
            model_decision=decision,
        )

    def retrieve_available(self, plan: IntelligencePlan):
        return tuple(self.registry.retrieve(request) for request in plan.retrieval_requests)

    def answerability(self, plan: IntelligencePlan, evidence: Iterable[EvidenceItem]) -> RequestedFactCoverage:
        return assess_requested_fact_coverage(plan.understanding, plan.route, evidence)


__all__ = ["IntelligencePlan", "DentalIntelligenceCore"]
