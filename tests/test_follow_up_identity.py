from types import SimpleNamespace

from drjavanbot.ai.config import AIConfig
from drjavanbot.telegram.app import TelegramBotApp




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
