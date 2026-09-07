from __future__ import annotations
import logging
import threading
import time
from typing import Any

from drjavanbot.ai.orchestrator import AIConfigurationError
from drjavanbot.ai.provider import AuthenticationError, ProviderResponseError, ProviderTimeoutError, ProviderUnavailableError, RateLimitError
from .api import TelegramAPI, TelegramAPIError
from .config import ALLOWED_MODELS, TelegramConfig
from .rendering import answer_chunks, html_escape, inline_keyboard, sources_page
from .contracts import BotServices, IndexNotReadyError
from .state import BotStateStore

_LOG = logging.getLogger(__name__)

class TelegramBotApp:
    def __init__(self, *, api: TelegramAPI, owner_id: int, services: BotServices, state: BotStateStore, config: TelegramConfig) -> None:
        self.api = api
        self.owner_id = int(owner_id)
        self.services = services
        self.state = state
        self.config = config

    def handle_update(self, update: dict[str, Any]) -> None:
        update_id = int(update.get("update_id", -1))
        if update_id >= 0 and not self.state.mark_update_once(update_id): return
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            self._handle_callback(callback); return
        message = update.get("message")
        if isinstance(message, dict): self._handle_message(message)

    def _handle_message(self, message: dict) -> None:
        chat = message.get("chat") or {}; user = message.get("from") or {}
        chat_id = int(chat.get("id")); user_id = int(user.get("id")); message_id = int(message.get("message_id"))
        chat_type = str(chat.get("type") or "")
        text = message.get("text")
        if not isinstance(text, str): return
        stripped = text.strip()
        is_private = chat_type == "private"

        if user_id == self.owner_id and is_private and self.state.active_flow(user_id) == "await_avalai_key":
            if stripped.startswith("/cancel"):
                self.state.clear_flow(user_id); self.api.send_message(chat_id, "لغو شد."); return
            try:
                self.api.delete_message(chat_id, message_id)
            except TelegramAPIError:
                pass
            self.state.clear_flow(user_id)
            try:
                ok = self.services.set_api_key(stripped)
            except Exception as exc:
                _LOG.warning("avalai_key_validation_failed error_class=%s", type(exc).__name__)
                self.api.send_message(chat_id, "❌ اعتبارسنجی کلید انجام نشد. کلید قبلی، اگر وجود داشته باشد، حفظ شده است.")
                return
            self.api.send_message(chat_id, "✅ کلید AvalAI اعتبارسنجی و ذخیره شد." if ok else "❌ کلید نامعتبر بود و ذخیره نشد.")
            return

        if stripped.startswith("/"):
            self._handle_command(chat_id, user_id, is_private, stripped); return
        self._handle_question(chat_id, user_id, stripped)

    def _handle_command(self, chat_id: int, user_id: int, is_private: bool, text: str) -> None:
        command, *rest = text.split(maxsplit=1)
        command = command.split("@", 1)[0].casefold()
        if command == "/start":
            self.api.send_message(chat_id, "🦷 <b>DrJavanBot</b>\nسؤال را بفرستید؛ پاسخ فقط بر پایه آرشیو گروه تولید می‌شود.")
            return
        if command == "/help":
            self.api.send_message(chat_id, self._help_text(user_id)); return
        if command in {"/settings", "/health", "/stats", "/reindex", "/allow", "/deny"}:
            if not self._require_owner_private(chat_id, user_id, is_private): return
        if command == "/settings": self._show_settings(chat_id); return
        if command == "/health": self.api.send_message(chat_id, self._health_text()); return
        if command == "/stats": self.api.send_message(chat_id, self._stats_text()); return
        if command == "/reindex": self._start_reindex(chat_id); return
        if command in {"/allow", "/deny"}:
            if not rest:
                self.api.send_message(chat_id, f"استفاده: <code>{command} TELEGRAM_USER_ID</code>"); return
            try: target = int(rest[0].strip())
            except ValueError:
                self.api.send_message(chat_id, "شناسه باید عددی باشد."); return
            if target <= 0: self.api.send_message(chat_id, "شناسه نامعتبر است."); return
            if command == "/allow": self.state.add_allowed_user(target); msg = "✅ به allowlist اضافه شد."
            else: self.state.remove_allowed_user(target); msg = "✅ از allowlist حذف شد."
            self.api.send_message(chat_id, msg); return
        self.api.send_message(chat_id, "دستور شناخته نشد. /help")

    def _handle_question(self, chat_id: int, user_id: int, question: str) -> None:
        if not self.state.is_allowed(user_id, self.owner_id):
            self.api.send_message(chat_id, "⛔️ دسترسی به این ربات برای حساب شما فعال نیست."); return
        if not question:
            self.api.send_message(chat_id, "سؤال خالی است."); return
        if len(question) > self.config.max_question_chars:
            self.api.send_message(chat_id, f"سؤال بیش از حد طولانی است. حداکثر {self.config.max_question_chars} نویسه."); return
        if not self.state.consume_rate_slot(user_id, owner_id=self.owner_id):
            self.api.send_message(chat_id, "⏳ تعداد درخواست‌های شما در یک دقیقه بیش از حد مجاز است."); return
        if not self.services.ai_configured():
            self.api.send_message(chat_id, "⚙️ سرویس AI هنوز توسط مدیر تنظیم نشده است."); return
        try: self.api.send_chat_action(chat_id, "typing")
        except TelegramAPIError: pass
        started = time.perf_counter()
        try:
            answer = self.services.answer(question)
            latency = (time.perf_counter() - started) * 1000
            self.state.record_question(user_id, success=True, latency_ms=latency, cache_hit=bool(answer.cache_hit), ai_calls=int(answer.ai_calls))
            chunks = answer_chunks(answer)
            for idx, chunk in enumerate(chunks):
                markup = None
                if idx == len(chunks)-1 and (answer.cited_message_ids or answer.source_refs):
                    items = self.services.source_details(answer.cited_message_ids, answer.source_refs)
                    if items:
                        sid = self.state.create_source_session(user_id, items, self.config.source_session_ttl_seconds)
                        markup = inline_keyboard([[('🔎 منابع', f'src:{sid}:0')]])
                self.api.send_message(chat_id, chunk, reply_markup=markup)
        except AIConfigurationError:
            self._record_failure(user_id, started, "AIConfigurationError"); self.api.send_message(chat_id, "⚙️ سرویس AI هنوز توسط مدیر تنظیم نشده است.")
        except AuthenticationError:
            self.state.set_provider_auth_failed(True); self._record_failure(user_id, started, "AuthenticationError"); self.api.send_message(chat_id, "🔑 اتصال AvalAI نیازمند بررسی مدیر است.")
        except RateLimitError:
            self._record_failure(user_id, started, "RateLimitError"); self.api.send_message(chat_id, "⏳ سرویس AI فعلاً محدودیت درخواست دارد. کمی بعد دوباره تلاش کنید.")
        except ProviderTimeoutError:
            self._record_failure(user_id, started, "ProviderTimeoutError"); self.api.send_message(chat_id, "⌛️ پاسخ سرویس AI در زمان مقرر نرسید.")
        except (ProviderUnavailableError, ProviderResponseError):
            self._record_failure(user_id, started, "ProviderError"); self.api.send_message(chat_id, "⚠️ سرویس AI موقتاً در دسترس نیست.")
        except IndexNotReadyError:
            self._record_failure(user_id, started, "IndexNotReadyError"); self.api.send_message(chat_id, "🗂 ایندکس آرشیو هنوز آماده نیست.")
        except Exception as exc:
            self._record_failure(user_id, started, type(exc).__name__)
            _LOG.exception("question_failed error_class=%s", type(exc).__name__)
            self.api.send_message(chat_id, "⚠️ خطای داخلی رخ داد. جزئیات حساس نمایش داده نمی‌شود.")

    def _record_failure(self, user_id: int, started: float, error_class: str) -> None:
        self.state.record_question(user_id, success=False, latency_ms=(time.perf_counter()-started)*1000, cache_hit=False, ai_calls=0, error_class=error_class)

    def _handle_callback(self, cb: dict) -> None:
        cqid = str(cb.get("id") or ""); user = cb.get("from") or {}; msg = cb.get("message") or {}; chat = msg.get("chat") or {}
        user_id = int(user.get("id")); chat_id = int(chat.get("id")); message_id = int(msg.get("message_id")); is_private = chat.get("type") == "private"
        data = str(cb.get("data") or "")
        try: self.api.answer_callback(cqid)
        except TelegramAPIError: pass
        if data.startswith("src:"):
            self._sources_callback(chat_id, message_id, user_id, data); return
        if not (user_id == self.owner_id and is_private):
            self.api.send_message(chat_id, "⛔️ این عملیات فقط برای مالک و در گفت‌وگوی خصوصی مجاز است."); return
        if data == "settings": self._show_settings(chat_id, message_id); return
        if data == "setkey":
            self.state.begin_flow(user_id, "await_avalai_key", self.config.key_entry_timeout_seconds)
            self.api.send_message(chat_id, "کلید AvalAI را در همین گفت‌وگوی خصوصی بفرستید. پیام حاوی کلید بلافاصله حذف می‌شود. /cancel برای لغو."); return
        if data == "remove_key":
            self.api.edit_message_text(chat_id, message_id, "حذف کلید AvalAI؟", reply_markup=inline_keyboard([[('✅ حذف', 'remove_key_yes'),('انصراف', 'settings')]])); return
        if data == "remove_key_yes":
            self.services.remove_api_key(); self.api.edit_message_text(chat_id,message_id,"✅ کلید حذف شد.",reply_markup=inline_keyboard([[('بازگشت','settings')]])); return
        if data == "testai":
            try: ok = self.services.test_ai(); msg2 = "✅ اتصال AvalAI سالم است." if ok else "❌ کلید تنظیم نشده یا نامعتبر است."
            except Exception as exc: _LOG.warning("avalai_test_failed error_class=%s",type(exc).__name__); msg2="⚠️ تست اتصال AvalAI ناموفق بود."
            self.api.edit_message_text(chat_id,message_id,msg2,reply_markup=inline_keyboard([[('بازگشت','settings')]])); return
        if data == "models":
            rows=[[(('✅ ' if self.services.model()==m else '')+m, f'model:{m}')] for m in ALLOWED_MODELS]; rows.append([('بازگشت','settings')])
            self.api.edit_message_text(chat_id,message_id,"<b>مدل AvalAI</b>",reply_markup=inline_keyboard(rows)); return
        if data.startswith("model:"):
            model=data.split(":",1)[1]
            if model not in ALLOWED_MODELS: self.api.send_message(chat_id,"مدل مجاز نیست."); return
            self.services.set_model(model); self.api.edit_message_text(chat_id,message_id,f"✅ مدل: <code>{html_escape(model)}</code>",reply_markup=inline_keyboard([[('بازگشت','settings')]])); return
        if data == "health": self.api.edit_message_text(chat_id,message_id,self._health_text(),reply_markup=inline_keyboard([[('بازگشت','settings')]])); return
        if data == "stats": self.api.edit_message_text(chat_id,message_id,self._stats_text(),reply_markup=inline_keyboard([[('بازگشت','settings')]])); return
        if data == "indexstats": self.api.edit_message_text(chat_id,message_id,self._index_stats_text(),reply_markup=inline_keyboard([[('بازگشت','settings')]])); return
        if data == "cache":
            s=self.services.cache.stats(); txt=f"<b>Cache</b>\nEntries: {s.entries}\nHits: {s.hits}\nMisses: {s.misses}\nExpired: {s.expired_entries}"
            self.api.edit_message_text(chat_id,message_id,txt,reply_markup=inline_keyboard([[('🧹 پاک‌کردن','clear_cache'),('بازگشت','settings')]])); return
        if data == "clear_cache":
            n=self.services.clear_cache(); self.api.edit_message_text(chat_id,message_id,f"✅ {n} cache entry پاک شد.",reply_markup=inline_keyboard([[('بازگشت','settings')]])); return
        if data == "access": self._show_access(chat_id,message_id); return
        if data.startswith("access:"):
            mode=data.split(":",1)[1]; self.state.set_access_mode(mode); self._show_access(chat_id,message_id); return
        if data.startswith("rate:"):
            self.state.set_rate_limit_per_minute(int(data.split(":",1)[1])); self._show_access(chat_id,message_id); return
        if data == "reindex": self._start_reindex(chat_id); return

    def _show_settings(self, chat_id: int, message_id: int | None = None) -> None:
        status = "✅ تنظیم شده" if self.services.ai_configured() else "❌ تنظیم نشده"
        text = f"<b>تنظیمات مالک</b>\nAvalAI: {status}\nModel: <code>{html_escape(self.services.model())}</code>\nAccess: <code>{self.state.access_mode()}</code>"
        kb = inline_keyboard([[('🔑 تنظیم/تعویض API Key','setkey'),('🧪 تست AvalAI','testai')],[('🗑 حذف API Key','remove_key'),('🤖 مدل','models')],[('📊 آمار','stats'),('❤️ Health','health')],[('🗂 Reindex','reindex'),('📚 Index','indexstats')],[('🧹 Cache','cache')],[('👥 دسترسی/Rate','access')]])
        if message_id is None: self.api.send_message(chat_id,text,reply_markup=kb)
        else: self.api.edit_message_text(chat_id,message_id,text,reply_markup=kb)

    def _show_access(self, chat_id: int, message_id: int) -> None:
        mode=self.state.access_mode(); rate=self.state.rate_limit_per_minute(); allowed=self.state.allowed_users()
        txt=f"<b>دسترسی</b>\nMode: <code>{mode}</code>\nRate: {rate}/min\nAllowlist: {len(allowed)} کاربر"
        rows=[[('Owner only','access:owner_only'),('Allowlist','access:allowlist'),('Public','access:public')],[('3/min','rate:3'),('6/min','rate:6'),('12/min','rate:12')],[('بازگشت','settings')]]
        self.api.edit_message_text(chat_id,message_id,txt,reply_markup=inline_keyboard(rows))

    def _sources_callback(self, chat_id: int, message_id: int, user_id: int, data: str) -> None:
        try: _, sid, page_raw = data.split(":",2); page=int(page_raw)
        except Exception: return
        items=self.state.get_source_session(sid,user_id)
        if items is None:
            return
        text,total=sources_page(items,page)
        nav=[]
        if page>0: nav.append(('⬅️ قبلی',f'src:{sid}:{page-1}'))
        if page+1<total: nav.append(('بعدی ➡️',f'src:{sid}:{page+1}'))
        self.api.edit_message_text(chat_id,message_id,text,reply_markup=inline_keyboard([nav]) if nav else None)

    def _require_owner_private(self, chat_id: int, user_id: int, is_private: bool) -> bool:
        if user_id == self.owner_id and is_private: return True
        self.api.send_message(chat_id,"⛔️ این دستور فقط برای مالک و در گفت‌وگوی خصوصی مجاز است."); return False

    def _help_text(self, user_id: int) -> str:
        base="سؤال متنی بفرستید. پاسخ فقط از آرشیو گروه استخراج می‌شود.\n/start — شروع\n/help — راهنما"
        if user_id==self.owner_id: base += "\n/settings — پنل مالک\n/health — سلامت\n/stats — آمار\n/reindex — بازسازی ایندکس\n/allow ID و /deny ID — allowlist"
        return base

    def _health_text(self) -> str:
        h=self.services.health(); idx=h.get('index') or {}; idx_ok=bool(idx.get('healthy'))
        return f"<b>Health</b>\nBot: ✅\nIndex: {'✅' if idx_ok else '❌'}\nAI key: {'✅' if h.get('ai_configured') else '❌'}\nAuth: {'❌ نیازمند بررسی' if h.get('provider_auth_failed') else '✅/نامشخص'}\nModel: <code>{html_escape(h.get('model'))}</code>"

    def _stats_text(self) -> str:
        s=self.services.stats(); b=s['bot']; a=s['ai']; idx=s.get('index') or {}
        return (f"<b>آمار</b>\nQuestions: {b.questions} | success {b.successes} | fail {b.failures}\nCache hits: {b.cache_hits}\nAI calls: {a.calls} | success {a.successes} | fail {a.failures}\nTokens in/out: {a.input_tokens}/{a.output_tokens}\nCached input: {a.cached_input_tokens}\nCost IRT: {a.cost_irt:.2f}\nAvg AI latency: {a.average_latency_ms:.0f} ms\nIndex messages: {idx.get('messages','—')}\nAccess: <code>{s['access_mode']}</code> | Rate: {s['rate_limit_per_minute']}/min\nLast reindex: {html_escape(s.get('last_reindex_at') or '—')}")

    def _index_stats_text(self) -> str:
        s=self.services.stats(); idx=s.get('index') or {}
        return (f"<b>Index</b>\nSchema: {html_escape(idx.get('schema_version','—'))}\nFiles: {html_escape(idx.get('archive_files','—'))}\nMessages: {html_escape(idx.get('messages','—'))}\nAuthors: {html_escape(idx.get('authors','—'))}\nDB bytes: {html_escape(idx.get('db_size_bytes','—'))}\nLast reindex: {html_escape(s.get('last_reindex_at') or '—')}")

    def _start_reindex(self, chat_id: int) -> None:
        self.api.send_message(chat_id,"🔄 Reindex شروع شد. index سالم قبلی تا موفقیت build جدید حفظ می‌شود.")
        def work() -> None:
            try:
                report=self.services.reindex()
                if report is None: self.api.send_message(chat_id,"⏳ یک reindex دیگر در حال اجراست.")
                else: self.api.send_message(chat_id,f"✅ Reindex کامل شد. Files: {report.archive_files} | Messages: {report.messages}")
            except Exception as exc:
                _LOG.exception("reindex_failed error_class=%s",type(exc).__name__)
                self.api.send_message(chat_id,"❌ Reindex ناموفق بود؛ index سالم قبلی حفظ شده است.")
        threading.Thread(target=work,name="drjavan-reindex",daemon=True).start()
