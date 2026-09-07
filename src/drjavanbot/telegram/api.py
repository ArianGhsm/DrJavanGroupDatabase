from __future__ import annotations
from dataclasses import dataclass
import json
import socket
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

class TelegramAPIError(RuntimeError): pass
class TelegramNetworkError(TelegramAPIError): pass
class TelegramUnauthorizedError(TelegramAPIError): pass

@dataclass(frozen=True, slots=True)
class TelegramResponse:
    status: int
    body: bytes

class TelegramTransport(Protocol):
    def request(self, url: str, body: bytes, timeout: float) -> TelegramResponse: ...

class UrllibTelegramTransport:
    def request(self, url: str, body: bytes, timeout: float) -> TelegramResponse:
        req = Request(url=url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with urlopen(req, timeout=timeout) as response:
                return TelegramResponse(int(response.status), response.read())
        except HTTPError as exc:
            return TelegramResponse(int(exc.code), exc.read())
        except (URLError, socket.timeout, TimeoutError) as exc:
            raise TelegramNetworkError("Telegram network request failed") from exc

class TelegramAPI:
    """Minimal Bot API client. Token is never included in repr/log messages."""
    def __init__(self, token: str, *, transport: TelegramTransport | None = None, timeout_seconds: float = 35.0) -> None:
        if not token or any(ch.isspace() for ch in token):
            raise ValueError("invalid Telegram bot token")
        self._token = token
        self._transport = transport or UrllibTelegramTransport()
        self.timeout_seconds = timeout_seconds

    def call(self, method: str, **params: Any) -> Any:
        encoded: dict[str, str] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, (dict, list, tuple)):
                encoded[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            elif isinstance(value, bool):
                encoded[key] = "true" if value else "false"
            else:
                encoded[key] = str(value)
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        response = self._transport.request(url, urlencode(encoded).encode("utf-8"), self.timeout_seconds)
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except Exception as exc:
            raise TelegramAPIError("Telegram returned invalid JSON") from exc
        if response.status in (401, 403):
            raise TelegramUnauthorizedError("Telegram rejected bot credentials")
        if not isinstance(payload, dict) or not payload.get("ok"):
            code = payload.get("error_code") if isinstance(payload, dict) else response.status
            raise TelegramAPIError(f"Telegram Bot API request failed ({code})")
        return payload.get("result")

    def get_updates(self, *, offset: int | None, timeout: int) -> list[dict]:
        result = self.call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message", "callback_query"])
        return list(result or [])

    def send_message(self, chat_id: int, text: str, *, reply_markup: dict | None = None, parse_mode: str = "HTML") -> dict:
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=True)

    def edit_message_text(self, chat_id: int, message_id: int, text: str, *, reply_markup: dict | None = None, parse_mode: str = "HTML") -> dict:
        return self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=True)

    def answer_callback(self, callback_query_id: str, text: str | None = None, *, show_alert: bool = False) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_query_id, text=text, show_alert=show_alert)

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        return bool(self.call("deleteMessage", chat_id=chat_id, message_id=message_id))

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        self.call("sendChatAction", chat_id=chat_id, action=action)

    def get_me(self) -> dict:
        return dict(self.call("getMe") or {})

    def __repr__(self) -> str:
        return "TelegramAPI(token=<redacted>)"
