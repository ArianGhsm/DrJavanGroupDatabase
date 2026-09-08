from __future__ import annotations

from typing import Protocol

from .models import RetrievalRequest, RetrievalResult, SourceType


class RetrievalProvider(Protocol):
    source_type: str

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        ...


class UnavailableRetrievalProvider:
    """Stage-1 adapter for source contracts whose network implementation is Stage 2."""

    def __init__(self, source_type: str, reason: str = "stage2_adapter_not_configured") -> None:
        self.source_type = str(source_type)
        self.reason = reason

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if request.source_type != self.source_type:
            raise ValueError("request source does not match provider")
        return RetrievalResult(
            source_type=self.source_type,
            items=(),
            query_count=0,
            latency_ms=0.0,
            unavailable_reason=self.reason,
        )


class RetrievalRegistry:
    def __init__(self, providers: tuple[RetrievalProvider, ...] = ()) -> None:
        self._providers = {str(provider.source_type): provider for provider in providers}

    def register(self, provider: RetrievalProvider) -> None:
        self._providers[str(provider.source_type)] = provider

    def provider_for(self, source_type: str) -> RetrievalProvider:
        provider = self._providers.get(str(source_type))
        if provider is not None:
            return provider
        return UnavailableRetrievalProvider(str(source_type))

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        return self.provider_for(request.source_type).retrieve(request)


FUTURE_SOURCE_TYPES = (
    SourceType.DENTAL_KNOWLEDGE,
    SourceType.SCIENTIFIC,
    SourceType.CURRENT_WEB,
    SourceType.OFFICIAL,
)


__all__ = ["RetrievalProvider", "UnavailableRetrievalProvider", "RetrievalRegistry", "FUTURE_SOURCE_TYPES"]
