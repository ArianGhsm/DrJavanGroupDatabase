from drjavanbot.intelligence.conversation import ConversationQuestionContext
from drjavanbot.intelligence.understanding import understand_question


def test_followup_uses_recent_semantic_entity_but_marks_it_inferred():
    first=understand_question("در مورد دندانپزشکی و حقوق دندانپزشک صحبت کنیم")
    context=ConversationQuestionContext.from_understanding(first).to_question_context()
    follow=understand_question("حقوق تازه کارها چقدره؟",context=context)
    assert "salary" in follow.facets
    assert any(e.canonical_id=="dentistry" for e in follow.entities)
    assert follow.geography.country_code=="IR"
