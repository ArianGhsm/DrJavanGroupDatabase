import json

from test_stage4_telegram import FakeAPI, cb, make, msg


class RecordingAPI(FakeAPI):
    def __init__(self):
        super().__init__(); self.markups = []; self.toasts = []

    def edit_message_reply_markup(self, chat_id, message_id, *, reply_markup=None):
        self.markups.append((chat_id, message_id, reply_markup)); return {}

    def answer_callback(self, cqid, text=None, **kw):
        super().answer_callback(cqid, text, **kw); self.toasts.append(text)


def _buttons(markup):
    return [button["callback_data"] for row in (markup or {}).get("inline_keyboard", []) for button in row]


def _setup():
    td, state, api, services, app = make()
    api = RecordingAPI(); app.api = api; app.admin.api = api
    state.set_access_mode("public")
    return td, state, api, app


def test_answers_carry_rating_buttons_and_thumbs_up_is_recorded():
    td, state, api, app = _setup()
    app.handle_update(msg(1, 42, "private", "روکش زیرکونیا لق میزنه"))
    markup = api.sent[-1][2]["reply_markup"]
    data = _buttons(markup)
    assert any(d.startswith("src:") for d in data)
    up = next(d for d in data if d.endswith(":up"))
    assert len(up.encode()) <= 64                               # Telegram callback_data limit
    app.handle_update(cb(2, 42, up))
    assert state.feedback_summary()["up"] == 1
    assert api.toasts[-1] == "ممنون 🙏"
    remaining = _buttons(api.markups[-1][2])
    assert remaining and all(d.startswith("src:") for d in remaining)  # rating row gone, sources stay
    td.cleanup()


def test_thumbs_down_asks_why_and_records_the_reason_for_the_owner():
    td, state, api, app = _setup()
    app.handle_update(msg(1, 42, "private", "سؤال"))
    down = next(d for d in _buttons(api.sent[-1][2]["reply_markup"]) if d.endswith(":down"))
    app.handle_update(cb(2, 42, down))
    reasons = [d for d in _buttons(api.markups[-1][2]) if ":r:" in d]
    assert len(reasons) == 4
    app.handle_update(cb(3, 42, next(r for r in reasons if r.endswith(":missed"))))
    item = state.recent_negative_feedback()[0]
    assert item["question"] == "سؤال" and item["reason"] == "missed" and "پاسخ" in item["answer"]
    td.cleanup()


def test_only_the_asker_can_rate():
    td, state, api, app = _setup()
    app.handle_update(msg(1, 42, "private", "سؤال"))
    down = next(d for d in _buttons(api.sent[-1][2]["reply_markup"]) if d.endswith(":down"))
    app.handle_update(cb(2, 7, down))
    assert state.feedback_summary()["down"] == 0
    assert "فقط پرسنده" in api.toasts[-1]
    td.cleanup()


def test_owner_sees_feedback_in_the_panel():
    td, state, api, app = _setup()
    app.handle_update(msg(1, 42, "private", "بریج کانتی لیور"))
    down = next(d for d in _buttons(api.sent[-1][2]["reply_markup"]) if d.endswith(":down"))
    app.handle_update(cb(2, 42, down))
    app.handle_update(cb(3, 42, down.replace(":down", ":r:wrong")))
    app.handle_update(cb(4, app.owner_id, "adm:feedback"))
    screen = (api.edited[-1][2] if api.edited else api.sent[-1][1])
    assert "👎 1" in screen and "بریج کانتی لیور" in screen and "جواب اشتباه بود" in screen
    td.cleanup()
