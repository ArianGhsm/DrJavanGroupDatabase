from __future__ import annotations
from drjavanbot.ai.models import AnswerResult
from .models import SourceRoute, SourceType

def required_non_archive_sources(route: SourceRoute) -> tuple[str,...]:
    return tuple(source for source in route.required_sources if source != SourceType.ARCHIVE)

def stage1_source_pending_answer(route: SourceRoute, *, ai_calls:int=0) -> AnswerResult:
    required=required_non_archive_sources(route)
    return AnswerResult(direct_answer="این سؤال طبق Source Router به منبعی خارج از آرشیو نیاز دارد، اما adapter آن منبع در Stage 1 هنوز فعال نشده است. برای جلوگیری از پاسخ archive-only یا حدس مدل، پاسخ علمی/جاری ساخته نشد.",key_findings=(),disagreements=(),practical_conclusion=None,confidence="low",confidence_reason="stage2_source_adapter_required:"+",".join(required),cited_message_ids=(),source_refs=(),evidence_used_count=0,independent_authors_count=0,insufficient_evidence=True,safety_note_if_needed=None,cache_hit=False,ai_calls=ai_calls,expansion_used=False,evidence_pack_estimated_tokens=0)

__all__=["required_non_archive_sources","stage1_source_pending_answer"]
