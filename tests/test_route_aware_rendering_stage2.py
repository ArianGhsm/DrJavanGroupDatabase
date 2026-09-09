from drjavanbot.ai.models import AnswerResult,GroundedSourceClaim,SourceSupport
from drjavanbot.telegram.rendering import answer_rich_screen


def _answer(mode,source_type):
    claim=GroundedSourceClaim("answer","پاسخ مستقیم مستند",(SourceSupport("e1",source_type,"ref","عبارت دقیق منبع"),))
    return AnswerResult(direct_answer=claim.text,key_findings=(),disagreements=(),practical_conclusion=None,confidence="medium",confidence_reason="grounded",cited_message_ids=(),source_refs=("ref",),evidence_used_count=1,independent_authors_count=1,insufficient_evidence=False,safety_note_if_needed=None,source_mode=mode,grounded_source_claims=(claim,),external_sources=({"source_type":source_type,"title":"Source title","publication_year":2026,"source_ref":"ref"},))


def test_renderer_labels_scientific_and_current_routes_without_archive_only_footer():
    sci=answer_rich_screen(_answer("scientific","scientific")); cur=answer_rich_screen(_answer("current","current_web"))
    assert "پاسخ علمی مستند" in sci.rich_html and "منبع پاسخ فقط آرشیو" not in sci.rich_html
    assert "اطلاعات به‌روز" in cur.rich_html and "منبع پاسخ فقط آرشیو" not in cur.rich_html
