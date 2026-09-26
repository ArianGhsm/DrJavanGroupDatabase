from types import SimpleNamespace

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.orchestrator import _cache_key, _plan_identity
from drjavanbot.ai.planner import deterministic_fallback_plan
from drjavanbot.telegram.app import TelegramBotApp


def test_follow_up_answers_are_cached_per_resolved_topic():
    config = AIConfig.from_env()
    question = "نظرتون درباره‌ش چیه"
    implant = deterministic_fallback_plan("ایمپلنت")
    crown = deterministic_fallback_plan("روکش زیرکونیا")
    plain = _cache_key(question + _plan_identity(None), "idx", config)
    assert plain == _cache_key(question, "idx", config)
    keys = {_cache_key(question + _plan_identity(plan), "idx", config) for plan in (implant, crown)}
    assert len(keys) == 2 and plain not in keys


def test_user_id_reaches_the_service_without_progress_ui():
    seen = []

    class Services:
        def answer(self, question, *, user_id=None):
            seen.append(user_id)
            return "ok"

    app = SimpleNamespace(services=Services())
    assert TelegramBotApp._answer(app, "و قیمتش؟", user_id=42, progress=None) == "ok"
    assert seen == [42]


def test_legacy_services_without_user_id_still_work():
    class Services:
        def answer(self, question):
            return "ok"

    app = SimpleNamespace(services=Services())
    assert TelegramBotApp._answer(app, "q", user_id=1, progress=None) == "ok"
