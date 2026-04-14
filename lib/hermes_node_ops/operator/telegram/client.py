"""Minimal Telegram Bot API client using only stdlib urllib.

No python-telegram-bot dependency. Implements just the methods we need:
getUpdates (long-polling), sendMessage, answerCallbackQuery.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


TELEGRAM_API_BASE = "https://api.telegram.org"


@dataclass(frozen=True)
class TelegramUpdate:
    """A single update from Telegram getUpdates."""

    update_id: int
    message_id: int
    chat_id: int
    user_id: int
    username: str
    text: str
    is_callback: bool = False
    callback_data: str = ""
    callback_query_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TelegramClient:
    """Minimal Telegram Bot API client."""

    token: str
    timeout_seconds: int = 35

    def _url(self, method: str) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self.token}/{method}"

    def _call(self, method: str, params: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
        """Make an HTTPS POST to the Telegram API."""
        url = self._url(method)
        body = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8")[:500]
            except Exception:
                body = ""
            raise RuntimeError(f"Telegram API HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise RuntimeError(f"Telegram API unreachable: {exc}") from exc

        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data.get('description', 'unknown')}")
        return data

    def get_me(self) -> dict[str, Any]:
        """Verify the token and return bot info."""
        return self._call("getMe", {}, timeout=10).get("result", {})

    def get_updates(
        self,
        *,
        offset: int = 0,
        timeout: int = 30,
        allowed_updates: list[str] | None = None,
    ) -> list[TelegramUpdate]:
        """Long-poll for new updates."""
        params: dict[str, Any] = {"timeout": timeout}
        if offset:
            params["offset"] = offset
        if allowed_updates is not None:
            params["allowed_updates"] = allowed_updates

        data = self._call("getUpdates", params, timeout=timeout + 10)
        return [parse_update(u) for u in data.get("result", [])]

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "",
        reply_markup: dict[str, Any] | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        """Send a text message. Automatically chunks if over Telegram's 4096 char limit."""
        # Telegram limit is 4096 chars; we chunk conservatively at 3900.
        if len(text) > 3900:
            chunks = _chunk_text(text, 3900)
            last: dict[str, Any] = {}
            for i, chunk in enumerate(chunks):
                params: dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": disable_web_page_preview,
                }
                if parse_mode:
                    params["parse_mode"] = parse_mode
                # Only attach reply_markup to the last chunk
                if reply_markup and i == len(chunks) - 1:
                    params["reply_markup"] = reply_markup
                last = self._call("sendMessage", params, timeout=15).get("result", {})
            return last

        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_markup:
            params["reply_markup"] = reply_markup
        return self._call("sendMessage", params, timeout=15).get("result", {})

    def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str = "",
        show_alert: bool = False,
    ) -> None:
        """Acknowledge an inline keyboard button press."""
        params: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            params["text"] = text
        if show_alert:
            params["show_alert"] = True
        self._call("answerCallbackQuery", params, timeout=10)

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        """Send a chat action (e.g. typing indicator)."""
        try:
            self._call("sendChatAction", {"chat_id": chat_id, "action": action}, timeout=5)
        except RuntimeError:
            pass  # non-critical, ignore failures


def parse_update(raw: dict[str, Any]) -> TelegramUpdate:
    """Parse a single Telegram update object into a TelegramUpdate."""
    update_id = int(raw.get("update_id", 0))

    # Handle callback queries (button presses)
    if "callback_query" in raw:
        cq = raw["callback_query"]
        msg = cq.get("message", {})
        chat = msg.get("chat", {})
        from_user = cq.get("from", {})
        return TelegramUpdate(
            update_id=update_id,
            message_id=int(msg.get("message_id", 0)),
            chat_id=int(chat.get("id", 0)),
            user_id=int(from_user.get("id", 0)),
            username=from_user.get("username", "") or "",
            text="",
            is_callback=True,
            callback_data=cq.get("data", "") or "",
            callback_query_id=cq.get("id", "") or "",
            raw=raw,
        )

    # Normal message
    msg = raw.get("message") or raw.get("edited_message") or {}
    chat = msg.get("chat", {})
    from_user = msg.get("from", {})
    return TelegramUpdate(
        update_id=update_id,
        message_id=int(msg.get("message_id", 0)),
        chat_id=int(chat.get("id", 0)),
        user_id=int(from_user.get("id", 0)),
        username=from_user.get("username", "") or "",
        text=msg.get("text", "") or "",
        raw=raw,
    )


def _chunk_text(text: str, size: int) -> list[str]:
    """Split text into chunks, preferring line boundaries."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > size:
        # Try to split on newline
        cut = remaining.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks
