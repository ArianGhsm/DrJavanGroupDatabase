from __future__ import annotations

import logging
import time

from drjavanbot.ai.orchestrator import AIConfigurationError
from drjavanbot.ai.provider import (
    AuthenticationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)

from .app_v2 import TelegramBotApp as OwnerTelegramBotApp
from .contracts import IndexNotReadyError
from .progress import QuestionProgressReporter
from .rendering import answer_chunks, inline_keyboard

_LOG = logging.getLogger(__name__)


class TelegramBotApp(OwnerTelegramBotApp):
    """Owner UI plus live, non-CoT question progress presentation."""

    def _handle_question(self, chat_id: int, user_id: int, question: str) -> None:
        if not getattr(self.config, "progress_ui_enabled", True):
            super()._handle_question(chat_id, user_id, question)
            return
        if not self.state.is_allowed(user_id, self.owner_id):
            self.api.send_message(chat_id, "⛔️ دسترسی به این ربات برای حساب شما فعال نیست.")
            return
        if not question:
            self.api.send_message(chat_id, "سؤال خالی است.")
            return
        if len(question) > self.config.max_question_chars:
            self.api.send_message(chat_id, f"سؤال بیش از حد طولانی است. حداکثر {self.config.max_question_chars} نویسه.")
            return
        if not self.state.consume_rate_slot(user_id, owner_id=self.owner_id):
            self.api.send_message(chat_id, "⏳ تعداد درخواست‌های شما در یک دقیقه بیش از حد مجاز است.")
            return
        if not self.services.ai_configured():
            self.api.send_message(chat_id, "⚙️ سرویس AI هنوز توسط مدیر تنظیم نشده است.")
            return

        reporter = QuestionProgressReporter(self.api, chat_id)
        reporter.start()
        started = time.perf_counter()
        try:
            progressive = getattr(self.services, "answer_with_progress", None)
            if callable(progressive):
                answer = progressive(question, reporter.on_event)
            else:
                # Compatibility with test/custom service implementations. Runtime
                # services always provide the progressive capability.
                answer = self.services.answer(question)

            latency = (time.perf_counter() - started) * 1000
            self.state.record_question(
                user_id,
                success=True,
                latency_ms=latency,
                cache_hit=bool(answer.cache_hit),
                ai_calls=int(answer.ai_calls),
            )
            chunks = answer_chunks(answer)
            for idx, chunk in enumerate(chunks):
                markup = None
                if idx == len(chunks) - 1 and (answer.cited_message_ids or answer.source_refs):
                    items = self.services.source_details(answer.cited_message_ids, answer.source_refs)
                    if items:
                        sid = self.state.create_source_session(
                            user_id,
                            items,
                            self.config.source_session_ttl_seconds,
                        )
                        markup = inline_keyboard([[("🔎 منابع", f"src:{sid}:0")]])
                self._send_answer_chunk(chat_id, chunk, reply_markup=markup)
        except AIConfigurationError:
            self._record_failure(user_id, started, "AIConfigurationError")
            self.api.send_message(chat_id, "⚙️ سرویس AI هنوز توسط مدیر تنظیم نشده است.")
        except AuthenticationError:
            self.state.set_provider_auth_failed(True)
            self._record_failure(user_id, started, "AuthenticationError")
            self.api.send_message(chat_id, "🔑 اتصال AvalAI نیازمند بررسی مدیر است.")
        except RateLimitError:
            self._record_failure(user_id, started, "RateLimitError")
            self.api.send_message(chat_id, "⏳ سرویس AI فعلاً محدودیت درخواست دارد. کمی بعد دوباره تلاش کنید.")
        except ProviderTimeoutError:
            self._record_failure(user_id, started, "ProviderTimeoutError")
            self.api.send_message(chat_id, "⌛️ پاسخ سرویس AI در زمان مقرر نرسید.")
        except (ProviderUnavailableError, ProviderResponseError):
            self._record_failure(user_id, started, "ProviderError")
            self.api.send_message(chat_id, "⚠️ سرویس AI موقتاً در دسترس نیست.")
        except IndexNotReadyError:
            self._record_failure(user_id, started, "IndexNotReadyError")
            self.api.send_message(chat_id, "🗂 ایندکس آرشیو هنوز آماده نیست.")
        except Exception as exc:
            self._record_failure(user_id, started, type(exc).__name__)
            _LOG.exception("question_failed error_class=%s", type(exc).__name__)
            self.api.send_message(chat_id, "⚠️ خطای داخلی رخ داد. جزئیات حساس نمایش داده نمی‌شود.")
        finally:
            # The status message is transient. It remains visible during long
            # planner/search/model waits, then disappears after answer/error UI is
            # already present so the chat does not accumulate progress clutter.
            reporter.close()


__all__ = ["TelegramBotApp"]
