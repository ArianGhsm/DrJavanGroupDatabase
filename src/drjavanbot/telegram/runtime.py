from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import logging
import signal
import threading

from drjavanbot.ai.config import AIConfig
from drjavanbot.config import Settings
from .api import TelegramAPI, TelegramAPIError, TelegramNetworkError
from .app_v2 import TelegramBotApp
from .config import TelegramConfig
from .services import RuntimeServices
from .state import BotStateStore

_LOG = logging.getLogger(__name__)

_PUBLIC_COMMANDS = [
    {"command": "start", "description": "شروع"},
    {"command": "help", "description": "راهنما"},
]
_OWNER_COMMANDS = [
    {"command": "start", "description": "شروع و پنل مالک"},
    {"command": "panel", "description": "پنل مالک"},
    {"command": "settings", "description": "تنظیمات مالک"},
    {"command": "health", "description": "سلامت سرویس"},
    {"command": "stats", "description": "آمار"},
    {"command": "reindex", "description": "بازسازی ایندکس"},
    {"command": "update", "description": "آپدیت امن نرم‌افزار"},
    {"command": "errors", "description": "خطاهای اخیر"},
    {"command": "help", "description": "راهنما"},
]


class PollingRunner:
    def __init__(self, app: TelegramBotApp, api: TelegramAPI, config: TelegramConfig) -> None:
        self.app = app; self.api = api; self.config = config
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=config.worker_count, thread_name_prefix="drjavan-update")

    def stop(self, *_args) -> None: self.stop_event.set()

    def _configure_command_menus(self) -> None:
        setter = getattr(self.api, "set_my_commands", None)
        if not callable(setter):
            _LOG.warning("telegram_command_menu_unavailable")
            return
        try:
            setter(_PUBLIC_COMMANDS)
            setter(
                _OWNER_COMMANDS,
                scope={"type": "chat", "chat_id": self.app.owner_id},
            )
            _LOG.info("telegram_command_menus_configured owner_id=%d", self.app.owner_id)
        except TelegramAPIError as exc:
            # Command-menu discoverability must never prevent the bot from starting.
            _LOG.warning("telegram_command_menu_failed error_class=%s", type(exc).__name__)

    def run(self) -> None:
        offset: int | None = None
        me = self.api.get_me(); _LOG.info("telegram_bot_started bot_id=%s", me.get("id"))
        self._configure_command_menus()
        try:
            while not self.stop_event.is_set():
                try:
                    updates = self.api.get_updates(offset=offset, timeout=self.config.poll_timeout_seconds)
                    batch=[]
                    for update in updates:
                        try: uid=int(update.get("update_id",-1))
                        except (TypeError,ValueError): uid=-1
                        batch.append((uid,self.executor.submit(self.app.handle_update,update)))
                    failed_ids=[]; completed_ids=[]
                    for uid,future in batch:
                        try:
                            future.result()
                            if uid>=0: completed_ids.append(uid)
                        except Exception as exc:
                            _LOG.exception("telegram_update_failed update_id=%s error_class=%s",uid,type(exc).__name__)
                            if uid>=0: failed_ids.append(uid)
                    if failed_ids:
                        offset=min(failed_ids); self.stop_event.wait(1.0)
                    elif completed_ids:
                        offset=max(completed_ids)+1
                except TelegramNetworkError:
                    if not self.stop_event.wait(2.0): _LOG.warning("telegram_poll_network_error")
                except TelegramAPIError as exc:
                    _LOG.error("telegram_poll_api_error error_class=%s",type(exc).__name__)
                    if self.stop_event.wait(5.0): break
        finally:
            self.executor.shutdown(wait=True,cancel_futures=False); _LOG.info("telegram_bot_stopped")


def build_runtime() -> tuple[PollingRunner, TelegramBotApp]:
    settings=Settings.from_env(require_runtime=True); tg=TelegramConfig.from_env()
    assert settings.telegram_bot_token is not None and settings.telegram_owner_id is not None
    state=BotStateStore(settings.data_dir/"bot_state.sqlite3",default_rate_limit=tg.default_rate_limit_per_minute)
    services=RuntimeServices(archive_dir=settings.archive_dir,data_dir=settings.data_dir,cache_dir=settings.cache_dir,secret_dir=settings.data_dir.parent/"secrets",base_ai_config=AIConfig.from_env(),state=state)
    api=TelegramAPI(settings.telegram_bot_token,timeout_seconds=float(tg.poll_timeout_seconds+10))
    app=TelegramBotApp(api=api,owner_id=settings.telegram_owner_id,services=services,state=state,config=tg)
    return PollingRunner(app,api,tg),app


def main() -> int:
    settings=Settings.from_env(require_runtime=True)
    logging.basicConfig(level=getattr(logging,settings.log_level,logging.INFO),format="%(asctime)s %(levelname)s %(name)s %(message)s")
    runner,_=build_runtime()
    for sig in (signal.SIGINT,signal.SIGTERM):
        try: signal.signal(sig,runner.stop)
        except (ValueError,OSError): pass
    runner.run(); return 0
