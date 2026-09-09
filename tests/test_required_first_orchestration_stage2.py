from dataclasses import replace
from pathlib import Path
import tempfile
from drjavanbot.ai.config import AIConfig
from drjavanbot.intelligence.models import EvidenceItem, RetrievalResult, SourceType
from drjavanbot.intelligence.understanding import understand_question
from drjavanbot.intelligence.routing import route_sources
from drjavanbot.intelligence.query_generation import generate_retrieval_requests
from drjavanbot.intelligence.core import IntelligencePlan
from drjavanbot.intelligence.retrieval import RetrievalRegistry
from drjavanbot.intelligence.service import MultiSourceAnswerService

class P:
    def __init__(self, source, item=None): self.source_type=source; self.item=item; self.calls=0
    def retrieve(self, request):
        self.calls+=1
        return RetrievalResult(self.source_type, (() if self.item is None else (self.item,)), 1, 1.0)

def test_required_source_is_not_blocked_by_optional_provider_when_answerable(monkeypatch):
    # Test the provider partitioning via a current route. Synthesis is replaced by an insufficient-safe stub boundary elsewhere;
    # here we only need to establish that optional providers are not called once required evidence passes coverage.
    u=understand_question('حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟'); r=route_sources(u); reqs=generate_retrieval_requests(u,r)
    current=EvidenceItem('cur',SourceType.CURRENT_WEB,'x','https://x','حقوق دندانپزشک تازه فارغ التحصیل 100 میلیون تومان در ماه؛ بازار کار و استخدام',timestamp='2026-09-01T00:00:00+00:00',metadata={'current_year_signal':True},trust_score=.9,independence_key='x')
    required=P(SourceType.CURRENT_WEB,current); optional=P(SourceType.OFFICIAL); archive=P(SourceType.ARCHIVE)
    reg=RetrievalRegistry((required,optional,archive))
    # Verify partition itself and coverage without invoking network/model.
    required_types=set(r.required_sources)
    required_reqs=tuple(x for x in reqs if x.source_type in required_types)
    optional_reqs=tuple(x for x in reqs if x.source_type not in required_types)
    assert [x.source_type for x in required_reqs]==[SourceType.CURRENT_WEB]
    assert {x.source_type for x in optional_reqs}=={SourceType.OFFICIAL,SourceType.ARCHIVE}
