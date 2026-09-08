from pathlib import Path

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.models import ProviderResult, UsageMetrics
from drjavanbot.ai.orchestrator import ArchiveAnswerService, _failure_classification
from drjavanbot.ai.telemetry import TelemetryStore
from drjavanbot.ai.validation import OutputTruncatedError
from drjavanbot.ai.provider import _extract_finish_reason


def test_finish_reason_is_extracted_and_truncation_has_specific_failure_code():
    assert _extract_finish_reason({"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}) == "length"
    assert _failure_classification(OutputTruncatedError("truncated")) == ("structured_output_failure", "output_truncated")


def test_provider_result_finish_reason_is_persistable_in_metadata_only_telemetry(tmp_path: Path):
    store = TelemetryStore(tmp_path / "usage.sqlite3")
    store.record(
        request_type="question_intelligence",
        stage="planning",
        model="fast",
        latency_ms=12.0,
        success=False,
        result_class="structured_output_failure",
        reason_code="output_truncated",
        finish_reason="length",
        usage=UsageMetrics(input_tokens=10, output_tokens=20),
    )
    import sqlite3
    with sqlite3.connect(tmp_path / "usage.sqlite3") as con:
        row = con.execute("SELECT finish_reason,reason_code,input_tokens,output_tokens FROM ai_usage").fetchone()
    assert row == ("length", "output_truncated", 10, 20)


def test_provider_result_backwards_compatible_default_finish_reason():
    result = ProviderResult("{}", "m", UsageMetrics(), 1.0)
    assert result.finish_reason is None
