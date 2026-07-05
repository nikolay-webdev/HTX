from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .data import _ssl_context


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, chat_id: int):
        self._base_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id

    def _call(self, method: str, parameters: dict | None = None):
        body = urlencode(parameters or {}).encode("utf-8")
        request = Request(f"{self._base_url}/{method}", data=body, method="POST")
        try:
            with urlopen(request, timeout=25, context=_ssl_context()) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise TelegramError(f"Telegram HTTP {error.code}") from None
        except URLError as error:
            reason = getattr(error, "reason", "network error")
            raise TelegramError(f"Telegram network error: {reason}") from None
        if not payload.get("ok"):
            raise TelegramError(payload.get("description", "Telegram API error"))
        return payload["result"]

    def check(self) -> dict:
        return self._call("getMe")

    def send(self, text: str) -> None:
        self._call(
            "sendMessage",
            {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": "true"},
        )

    def updates(self, offset: int | None = None) -> list[dict]:
        parameters: dict[str, str | int] = {
            "timeout": 0,
            "limit": 100,
            "allowed_updates": json.dumps(["message"]),
        }
        if offset is not None:
            parameters["offset"] = offset
        return self._call("getUpdates", parameters)
