from __future__ import annotations
import logging,time
from .provider import AvalAIClient,ProviderResult,_extract_content,_extract_usage,_header,_optional_string
_LOG=logging.getLogger(__name__)
class DeepSeekV4AvalAIClient(AvalAIClient):
    """DeepSeek V4 adapter that explicitly disables thinking to constrain token cost/latency."""
    def chat_json(self,*,api_key:str,request_type:str,system_prompt:str,user_prompt:str,max_output_tokens:int)->ProviderResult:
        payload={"model":self.config.model,"messages":[{"role":"system","content":system_prompt},{"role":"user","content":user_prompt}],"temperature":0.1,"max_tokens":int(max_output_tokens),"response_format":{"type":"json_object"},"thinking":{"type":"disabled"}}
        started=time.perf_counter(); response,parsed=self._request_json(method="POST",path="/chat/completions",api_key=api_key,payload=payload); latency=(time.perf_counter()-started)*1000; content=_extract_content(parsed); usage=_extract_usage(parsed); model=str(parsed.get("model") or self.config.model); request_id=_header(response.headers,"x-request-id") or _optional_string(parsed.get("id")); _LOG.info("avalai_call request_type=%s model=%s thinking=disabled success=true input_tokens=%d cached_input_tokens=%d output_tokens=%d latency_ms=%.1f",request_type,model,usage.input_tokens,usage.cached_input_tokens,usage.output_tokens,latency); return ProviderResult(content=content,model=model,usage=usage,latency_ms=latency,request_id=request_id)
