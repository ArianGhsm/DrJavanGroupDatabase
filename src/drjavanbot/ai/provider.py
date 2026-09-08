from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import socket
import time
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import AIConfig
from .models import ProviderResult, UsageMetrics

_LOG = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    pass


class AuthenticationError(ProviderError):
    pass


class RateLimitError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class ProviderUnavailableError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


class TransportError(RuntimeError):
    pass


class TransportTimeout(TransportError):
    pass


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class HTTPTransport(Protocol):
    def request(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> TransportResponse:
        ...


class UrllibTransport:
    def request(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> TransportResponse:
        request = Request(url=url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return TransportResponse(int(response.status), response.read(), dict(response.headers.items()))
        except HTTPError as exc:
            return TransportResponse(int(exc.code), exc.read(), dict(exc.headers.items()) if exc.headers else {})
        except (socket.timeout, TimeoutError) as exc:
            raise TransportTimeout("provider request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise TransportTimeout("provider request timed out") from exc
            raise TransportError("provider network error") from exc


class AvalAIClient:
    """Small OpenAI-compatible AvalAI client with bounded retries and redacted logging."""

    def __init__(self, config: AIConfig, *, transport: HTTPTransport | None = None, sleep=time.sleep) -> None:
        self.config = config
        self.transport = transport or UrllibTransport()
        self._sleep = sleep

    def chat_json(
        self,
        *,
        api_key: str,
        request_type: str,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
    ) -> ProviderResult:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "reasoning_effort": self.config.reasoning_effort,
            "max_tokens": int(max_output_tokens),
            "response_format": {"type": "json_object"},
        }
        started = time.perf_counter()
        response, parsed = self._request_json(
            method="POST",
            path="/chat/completions",
            api_key=api_key,
            payload=payload,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        content = _extract_content(parsed)
        usage = _extract_usage(parsed)
        model = str(parsed.get("model") or self.config.model)
        request_id = _header(response.headers, "x-request-id") or _optional_string(parsed.get("id"))
        finish_reason = _extract_finish_reason(parsed)
        _LOG.info(
            "avalai_call request_type=%s model=%s success=true input_tokens=%d cached_input_tokens=%d output_tokens=%d latency_ms=%.1f",
            request_type, model, usage.input_tokens, usage.cached_input_tokens, usage.output_tokens, latency_ms,
        )
        return ProviderResult(content=content, model=model, usage=usage, latency_ms=latency_ms, request_id=request_id, finish_reason=finish_reason)

    def validate_api_key(self, api_key: str) -> bool:
        """Validate auth via the low-cost /models endpoint; never persists the candidate key."""
        try:
            self._request_json(method="GET", path="/models", api_key=api_key, payload=None)
            return True
        except AuthenticationError:
            return False

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        api_key: str,
        payload: dict | None,
    ) -> tuple[TransportResponse, dict]:
        if not api_key or "\n" in api_key or "\r" in api_key:
            raise AuthenticationError("AvalAI API key is missing or malformed")
        url = self.config.base_url.rstrip("/") + path
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Accept": "application/json", "Authorization": "Bearer " + api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"

        last_network_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.transport.request(method, url, headers, body, self.config.timeout_seconds)
            except TransportTimeout as exc:
                last_network_error = exc
                if attempt < self.config.max_retries:
                    self._backoff(attempt, None)
                    continue
                raise ProviderTimeoutError("AvalAI request timed out") from exc
            except TransportError as exc:
                last_network_error = exc
                if attempt < self.config.max_retries:
                    self._backoff(attempt, None)
                    continue
                raise ProviderUnavailableError("AvalAI network request failed") from exc

            status = response.status
            if status in (401, 403):
                raise AuthenticationError("AvalAI rejected the API key")
            if status == 429:
                if attempt < self.config.max_retries:
                    self._backoff(attempt, _header(response.headers, "retry-after"))
                    continue
                raise RateLimitError("AvalAI rate limit exceeded")
            if status >= 500:
                if attempt < self.config.max_retries:
                    self._backoff(attempt, _header(response.headers, "retry-after"))
                    continue
                raise ProviderUnavailableError(f"AvalAI server error ({status})")
            if not 200 <= status < 300:
                raise ProviderResponseError(f"AvalAI returned HTTP {status}")
            try:
                parsed = json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProviderResponseError("AvalAI returned invalid JSON") from exc
            if not isinstance(parsed, dict):
                raise ProviderResponseError("AvalAI JSON response must be an object")
            return response, parsed

        raise ProviderUnavailableError("AvalAI request failed") from last_network_error

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        wait = self.config.retry_base_seconds * (2 ** attempt)
        if retry_after:
            try:
                wait = max(wait, min(float(retry_after), 5.0))
            except ValueError:
                pass
        if wait > 0:
            self._sleep(wait)


def _extract_content(payload: dict) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderResponseError("AvalAI response is missing message content") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "".join(parts)
    raise ProviderResponseError("AvalAI message content is not text")


def _extract_finish_reason(payload: dict) -> str | None:
    try:
        value = payload["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return _optional_string(value)


def _extract_usage(payload: dict) -> UsageMetrics:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cost = payload.get("estimated_cost")
    if not isinstance(cost, dict):
        cost = usage.get("estimated_cost") if isinstance(usage.get("estimated_cost"), dict) else {}
    input_tokens = _int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    output_tokens = _int(usage.get("completion_tokens") or usage.get("output_tokens"))
    total = _int(usage.get("total_tokens")) or input_tokens + output_tokens
    cached = _int(prompt_details.get("cached_tokens") or usage.get("cached_tokens"))
    return UsageMetrics(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output_tokens,
        total_tokens=total,
        cost_irt=_float_or_none(cost.get("irt")),
        cost_unit=_optional_string(cost.get("unit")),
        exchange_rate=_float_or_none(cost.get("exchange_rate")),
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return str(value)
    return None


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_or_none(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _optional_string(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
