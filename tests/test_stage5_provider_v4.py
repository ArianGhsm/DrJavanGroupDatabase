from __future__ import annotations
import json

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.provider import TransportResponse
from drjavanbot.ai.provider_v4 import DeepSeekV4AvalAIClient


class CaptureTransport:
    def __init__(self) -> None:
        self.body: bytes | None = None

    def request(self, method, url, headers, body, timeout):
        self.body = body
        payload = {
            "id": "test",
            "model": "deepseek-v4-flash",
            "choices": [{"message": {"content": '{"ok":true}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
        return TransportResponse(status=200, body=json.dumps(payload).encode("utf-8"), headers={})


def test_deepseek_v4_runtime_explicitly_disables_thinking():
    transport = CaptureTransport()
    client = DeepSeekV4AvalAIClient(AIConfig(), transport=transport, sleep=lambda _: None)
    result = client.chat_json(
        api_key="test-key-not-secret",
        request_type="synthesis",
        system_prompt="fixed",
        user_prompt="evidence",
        max_output_tokens=100,
    )
    assert result.content == '{"ok":true}'
    assert transport.body is not None
    body = json.loads(transport.body)
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body
    assert body["model"] == "deepseek-v4-flash"
