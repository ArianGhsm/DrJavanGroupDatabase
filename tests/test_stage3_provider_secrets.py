from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile

import pytest

from drjavanbot.ai import AIConfig
from drjavanbot.ai.key_manager import AvalAIKeyManager
from drjavanbot.ai.provider import AuthenticationError, AvalAIClient, ProviderTimeoutError, RateLimitError, TransportResponse, TransportTimeout
from drjavanbot.secrets import AVALAI_API_KEY_SECRET, LocalFileSecretStore


class FakeTransport:
    def __init__(self,responses): self.responses=list(responses); self.calls=[]
    def request(self,method,url,headers,body,timeout):
        self.calls.append((method,url,dict(headers),body,timeout)); value=self.responses.pop(0)
        if isinstance(value,Exception): raise value
        return value


def response(status,payload,headers=None): return TransportResponse(status,json.dumps(payload).encode(),headers or {})


def chat_payload(content='{"x":1}'):
    return {"id":"req-1","model":"deepseek-v4-flash","choices":[{"message":{"content":content}}],
            "usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120,"prompt_tokens_details":{"cached_tokens":40}},
            "estimated_cost":{"irt":33.5,"unit":"IRT","exchange_rate":1234}}


def test_provider_retries_429_then_succeeds_and_extracts_usage():
    transport=FakeTransport([response(429,{"error":"rate"},{"Retry-After":"0"}),response(200,chat_payload())]); sleeps=[]
    client=AvalAIClient(AIConfig(max_retries=1,retry_base_seconds=0),transport=transport,sleep=sleeps.append)
    result=client.chat_json(api_key="secret-123456",request_type="synthesis",system_prompt="s",user_prompt="u",max_output_tokens=100)
    assert len(transport.calls)==2 and result.usage.cached_input_tokens==40 and result.usage.cost_irt==33.5 and result.usage.input_tokens==100 and result.usage.output_tokens==20


def test_provider_auth_error_is_not_retried():
    transport=FakeTransport([response(401,{"error":"no"})]); client=AvalAIClient(AIConfig(max_retries=2,retry_base_seconds=0),transport=transport,sleep=lambda _:None)
    with pytest.raises(AuthenticationError): client.chat_json(api_key="secret-123456",request_type="synthesis",system_prompt="s",user_prompt="u",max_output_tokens=100)
    assert len(transport.calls)==1


def test_provider_timeout_has_bounded_retries():
    transport=FakeTransport([TransportTimeout("x"),TransportTimeout("x"),TransportTimeout("x")]); client=AvalAIClient(AIConfig(max_retries=2,retry_base_seconds=0),transport=transport,sleep=lambda _:None)
    with pytest.raises(ProviderTimeoutError): client.chat_json(api_key="secret-123456",request_type="synthesis",system_prompt="s",user_prompt="u",max_output_tokens=100)
    assert len(transport.calls)==3


def test_api_key_never_appears_in_provider_log(caplog):
    key="super-secret-avalai-key"; transport=FakeTransport([response(200,chat_payload())]); client=AvalAIClient(AIConfig(max_retries=0),transport=transport)
    with caplog.at_level(logging.INFO): client.chat_json(api_key=key,request_type="synthesis",system_prompt="s",user_prompt="u",max_output_tokens=100)
    assert key not in caplog.text


def test_local_secret_store_uses_restricted_permissions_and_redacted_repr():
    key="very-secret-value"
    with tempfile.TemporaryDirectory() as td:
        store=LocalFileSecretStore(Path(td)/"secrets"); store.set_secret(AVALAI_API_KEY_SECRET,key); secret_path=Path(td)/"secrets"/AVALAI_API_KEY_SECRET
        assert store.get_secret(AVALAI_API_KEY_SECRET)==key
        if os.name=="posix": assert secret_path.stat().st_mode & 0o777==0o600 and secret_path.parent.stat().st_mode & 0o777==0o700
        assert key not in repr(store); assert store.delete_secret(AVALAI_API_KEY_SECRET); assert store.get_secret(AVALAI_API_KEY_SECRET) is None


class ValidatorClient:
    def __init__(self,valid): self.valid=valid; self.keys=[]
    def validate_api_key(self,key): self.keys.append(key); return self.valid


def test_key_manager_does_not_replace_good_key_when_new_key_invalid():
    with tempfile.TemporaryDirectory() as td:
        store=LocalFileSecretStore(Path(td)/"secrets"); store.set_secret(AVALAI_API_KEY_SECRET,"existing-good-key"); manager=AvalAIKeyManager(store,ValidatorClient(False))
        assert not manager.validate_and_store("candidate-bad-key") and store.get_secret(AVALAI_API_KEY_SECRET)=="existing-good-key"


def test_key_manager_validates_before_atomic_store():
    with tempfile.TemporaryDirectory() as td:
        store=LocalFileSecretStore(Path(td)/"secrets"); client=ValidatorClient(True); manager=AvalAIKeyManager(store,client)
        assert manager.validate_and_store("candidate-good-key") and client.keys==["candidate-good-key"] and store.get_secret(AVALAI_API_KEY_SECRET)=="candidate-good-key"


def test_provider_final_429_raises_rate_limit_error():
    transport=FakeTransport([response(429,{"error":"rate"}),response(429,{"error":"rate"})]); client=AvalAIClient(AIConfig(max_retries=1,retry_base_seconds=0),transport=transport,sleep=lambda _:None)
    with pytest.raises(RateLimitError): client.chat_json(api_key="secret-123456",request_type="synthesis",system_prompt="s",user_prompt="u",max_output_tokens=100)
    assert len(transport.calls)==2
