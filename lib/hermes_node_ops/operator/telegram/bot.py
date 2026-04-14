"""Telegram bot — long-polling loop that routes messages to the operator service.

This is a thin ingress layer. It reuses the existing operator backend,
tools, sessions, and constitution unchanged. Not a parallel agent stack.

One operator session per Telegram chat_id, persisted under
state/operator_sessions/telegram_{chat_id}/.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from ...config import Settings
from ...jsonl import append_jsonl, utcnow
from ...ledger import Ledger
from ..backend import ChatBackend, ToolCall, create_backend
from ..constitution import build_system_prompt
from ..session import (
    append_message,
    build_messages_for_backend,
    create_session,
    read_notes,
)
from ..tools import ToolExecutor, get_tool_definitions
from .client import TelegramClient, TelegramUpdate
from .formatter import (
    format_alert,
    format_assistant_response,
    format_pending_approvals,
    format_queue_status,
    format_task_list,
    format_task_status,
    format_tool_result_compact,
)


MAX_TOOL_ROUNDS = 8
OFFSET_FILE = "telegram_update_offset.json"
ALERT_CURSOR_FILE = "telegram_alert_cursor.json"


class TelegramBot:
    """Long-polling Telegram bot routing to the operator service."""

    def __init__(
        self,
        settings: Settings,
        backend: ChatBackend,
        client: TelegramClient,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.client = client
        self.repo_root = settings.repo_root
        self.state_dir = settings.ledger_db.parent
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.allowed_chat_ids = set(settings.telegram_allowed_chat_ids)
        self.tool_defs = get_tool_definitions()

    # -- main loop --

    def run_forever(self) -> None:
        """Run the long-polling loop forever."""
        self._log("bot_started", model=self.backend.model_name)
        offset = self._read_offset()
        last_alert_check = 0.0

        while True:
            # Alerts check
            now = time.time()
            if now - last_alert_check >= self.settings.telegram_alert_poll_seconds:
                try:
                    self._process_alerts()
                except Exception as exc:  # noqa: BLE001
                    self._log("alert_error", error=str(exc))
                last_alert_check = now

            # Long-poll updates
            try:
                updates = self.client.get_updates(
                    offset=offset,
                    timeout=self.settings.telegram_poll_timeout,
                )
            except RuntimeError as exc:
                self._log("poll_error", error=str(exc))
                time.sleep(5)
                continue

            for update in updates:
                offset = max(offset, update.update_id + 1)
                try:
                    self._handle_update(update)
                except Exception as exc:  # noqa: BLE001
                    self._log("handle_error", update_id=update.update_id, error=str(exc))
                    try:
                        self.client.send_message(
                            update.chat_id,
                            f"[error] {exc}",
                        )
                    except Exception:
                        pass

            self._write_offset(offset)

    # -- update routing --

    def _handle_update(self, update: TelegramUpdate) -> None:
        if not self._is_allowed(update):
            self._log(
                "update_denied",
                chat_id=update.chat_id,
                user_id=update.user_id,
                username=update.username,
            )
            if self.allowed_chat_ids:
                # Respond only if the list is non-empty; silence when token is fresh
                self.client.send_message(
                    update.chat_id,
                    f"unauthorized. your chat_id is {update.chat_id}",
                )
            return

        self._log(
            "update_received",
            chat_id=update.chat_id,
            user_id=update.user_id,
            is_callback=update.is_callback,
            text_preview=(update.text or update.callback_data)[:100],
        )

        if update.is_callback:
            self._handle_callback(update)
            return

        text = update.text.strip()
        if not text:
            return

        if text.startswith("/"):
            self._handle_slash(update, text)
            return

        self._handle_chat(update, text)

    # -- slash commands --

    def _handle_slash(self, update: TelegramUpdate, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        # Strip optional @botname suffix
        if "@" in cmd:
            cmd = cmd.split("@", 1)[0]

        executor = self._make_executor(update.chat_id)

        if cmd == "/start":
            self.client.send_message(
                update.chat_id,
                "hermes-node operator ready.\n"
                f"chat_id: {update.chat_id}\n"
                f"model: {self.backend.model_name}\n\n"
                "commands:\n"
                "  /status — queue overview\n"
                "  /pending — tasks awaiting approval\n"
                "  /approve <id> — approve a pending task\n"
                "  /task <id> — task detail\n"
                "  /runs — recent runs\n"
                "  /new — start a fresh session\n"
                "  /session — show current session id\n"
                "  /help — this message\n\n"
                "or just type a message and I'll think through it.",
            )
            return

        if cmd == "/help":
            self.client.send_message(
                update.chat_id,
                "commands: /status /pending /approve <id> /task <id> /runs /new /session",
            )
            return

        if cmd == "/status":
            raw = executor.dispatch("get_queue_status", {"limit": 5})
            try:
                data = json.loads(raw)
                self.client.send_message(update.chat_id, format_queue_status(data))
            except Exception:
                self.client.send_message(update.chat_id, raw[:3500])
            return

        if cmd == "/pending":
            raw = executor.dispatch("list_recent_tasks", {"limit": 30})
            try:
                tasks = json.loads(raw)
                self.client.send_message(update.chat_id, format_pending_approvals(tasks))
            except Exception:
                self.client.send_message(update.chat_id, raw[:3500])
            return

        if cmd == "/approve":
            if not arg:
                self.client.send_message(update.chat_id, "usage: /approve <task_id>")
                return
            raw = executor.dispatch(
                "approve_task",
                {"task_id": arg, "note": f"Approved via Telegram by chat {update.chat_id}"},
            )
            try:
                data = json.loads(raw)
                if data.get("approved"):
                    self.client.send_message(
                        update.chat_id,
                        f"approved {arg} -> {data.get('new_state','?')}",
                    )
                else:
                    self.client.send_message(
                        update.chat_id,
                        f"not approved: {data.get('error','?')}",
                    )
            except Exception:
                self.client.send_message(update.chat_id, raw[:3500])
            return

        if cmd == "/task":
            if not arg:
                self.client.send_message(update.chat_id, "usage: /task <task_id>")
                return
            raw = executor.dispatch("get_task_status", {"task_id": arg})
            try:
                data = json.loads(raw)
                if "error" in data:
                    self.client.send_message(update.chat_id, f"error: {data['error']}")
                else:
                    self.client.send_message(update.chat_id, format_task_status(data))
            except Exception:
                self.client.send_message(update.chat_id, raw[:3500])
            return

        if cmd == "/runs":
            raw = executor.dispatch("list_recent_runs", {"limit": 10})
            try:
                runs = json.loads(raw)
                from .formatter import format_run_list
                self.client.send_message(update.chat_id, format_run_list(runs))
            except Exception:
                self.client.send_message(update.chat_id, raw[:3500])
            return

        if cmd == "/new":
            # Rotate the session by creating a new one keyed to chat_id + timestamp
            self._rotate_session(update.chat_id)
            sid = self._session_id_for_chat(update.chat_id)
            self.client.send_message(update.chat_id, f"new session started: {sid}")
            return

        if cmd == "/session":
            sid = self._session_id_for_chat(update.chat_id)
            self.client.send_message(update.chat_id, f"session: {sid}")
            return

        self.client.send_message(update.chat_id, f"unknown command: {cmd}")

    # -- conversation (LLM with tool calls) --

    def _handle_chat(self, update: TelegramUpdate, text: str) -> None:
        sid = self._session_id_for_chat(update.chat_id)
        executor = self._make_executor(update.chat_id)

        # Record user message
        append_message(self.repo_root, sid, role="user", content=text)

        notes = read_notes(self.repo_root, sid)
        system_prompt = build_system_prompt(session_notes=notes)
        messages = build_messages_for_backend(
            self.repo_root, sid, system_prompt=system_prompt,
        )

        # Typing indicator
        self.client.send_chat_action(update.chat_id, "typing")

        rounds = 0
        tool_trace: list[str] = []
        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            try:
                response = self.backend.chat(
                    messages, tools=self.tool_defs, temperature=0.3,
                )
            except Exception as exc:  # noqa: BLE001
                err = f"[backend error] {exc}"
                append_message(self.repo_root, sid, role="assistant", content=err)
                self.client.send_message(update.chat_id, err[:1500])
                return

            if response.tool_calls:
                tc_serialized = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ]
                append_message(
                    self.repo_root, sid,
                    role="assistant",
                    content=response.content or "",
                    tool_calls=tc_serialized,
                )
                messages.append({
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": tc_serialized,
                })

                for tc in response.tool_calls:
                    tool_trace.append(f"-> {tc.name}")
                    result = executor.dispatch(tc.name, tc.arguments)
                    append_message(
                        self.repo_root, sid,
                        role="tool",
                        content=result,
                        tool_call_id=tc.id,
                        name=tc.name,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result,
                    })

                # Keep typing during tool rounds
                self.client.send_chat_action(update.chat_id, "typing")
                continue

            # No more tool calls — final assistant response
            content = format_assistant_response(response.content or "")
            append_message(self.repo_root, sid, role="assistant", content=content)

            reply = content if content else "(empty response)"
            if tool_trace:
                reply = reply + "\n\n" + " ".join(tool_trace[:10])
            self.client.send_message(update.chat_id, reply)
            return

        self.client.send_message(
            update.chat_id,
            "[tool call limit reached]",
        )

    def _handle_callback(self, update: TelegramUpdate) -> None:
        """Handle inline keyboard button presses."""
        data = update.callback_data
        self.client.answer_callback_query(update.callback_query_id, text="received")
        if data.startswith("approve:"):
            task_id = data.split(":", 1)[1]
            executor = self._make_executor(update.chat_id)
            raw = executor.dispatch(
                "approve_task",
                {"task_id": task_id, "note": "Approved via Telegram inline button"},
            )
            try:
                result = json.loads(raw)
                if result.get("approved"):
                    self.client.send_message(
                        update.chat_id,
                        f"approved {task_id} -> {result.get('new_state','?')}",
                    )
                else:
                    self.client.send_message(
                        update.chat_id,
                        f"not approved: {result.get('error','?')}",
                    )
            except Exception:
                self.client.send_message(update.chat_id, raw[:1500])

    # -- alert watcher --

    def _process_alerts(self) -> None:
        """Check ledger events since last cursor and send alerts for notable ones."""
        cursor = self._read_alert_cursor()
        alert_types = {
            "run.failed": "task.failed",
            "task.deferred_rate_limit": "task.deferred_rate_limit",
            "approval.requested": "task.pending_approval",
        }

        with Ledger(self.settings.ledger_db) as ledger:
            rows = ledger._conn.execute(
                """
                SELECT * FROM events
                WHERE id > ? AND event_type IN ({placeholders})
                ORDER BY id ASC LIMIT 50
                """.format(placeholders=",".join("?" for _ in alert_types)),
                (cursor, *alert_types.keys()),
            ).fetchall()

        if not rows:
            return

        for row in rows:
            event = dict(row)
            alert_type = alert_types.get(event["event_type"])
            if not alert_type:
                continue
            text = format_alert(alert_type, event)
            for chat_id in self.allowed_chat_ids:
                try:
                    self.client.send_message(chat_id, text)
                except Exception as exc:  # noqa: BLE001
                    self._log("alert_send_error", chat_id=chat_id, error=str(exc))
            cursor = max(cursor, int(event["id"]))

        self._write_alert_cursor(cursor)

    # -- auth / session helpers --

    def _is_allowed(self, update: TelegramUpdate) -> bool:
        if not self.allowed_chat_ids:
            return False
        return update.chat_id in self.allowed_chat_ids

    def _session_id_for_chat(self, chat_id: int) -> str:
        """Get or create a persistent session for a Telegram chat_id."""
        # Check for an active session marker
        marker_path = self.state_dir / f"telegram_session_{chat_id}.txt"
        if marker_path.exists():
            sid = marker_path.read_text(encoding="utf-8").strip()
            # Verify session dir exists
            session_dir = self.repo_root / "state" / "operator_sessions" / sid
            if session_dir.exists():
                return sid

        # Create a new session
        sid = create_session(self.repo_root, model=self.backend.model_name)
        marker_path.write_text(sid, encoding="utf-8")
        return sid

    def _rotate_session(self, chat_id: int) -> None:
        marker_path = self.state_dir / f"telegram_session_{chat_id}.txt"
        if marker_path.exists():
            marker_path.unlink()

    def _make_executor(self, chat_id: int) -> ToolExecutor:
        sid = self._session_id_for_chat(chat_id)
        return ToolExecutor(self.settings, self.repo_root, session_id=sid)

    # -- persistence helpers --

    def _offset_path(self) -> Path:
        return self.state_dir / OFFSET_FILE

    def _read_offset(self) -> int:
        p = self._offset_path()
        if not p.exists():
            return 0
        try:
            return int(json.loads(p.read_text(encoding="utf-8")).get("offset", 0))
        except (OSError, json.JSONDecodeError, ValueError):
            return 0

    def _write_offset(self, offset: int) -> None:
        p = self._offset_path()
        p.write_text(json.dumps({"offset": offset}), encoding="utf-8")

    def _alert_cursor_path(self) -> Path:
        return self.state_dir / ALERT_CURSOR_FILE

    def _read_alert_cursor(self) -> int:
        p = self._alert_cursor_path()
        if not p.exists():
            # Start from current max event id so first run doesn't replay history
            try:
                with Ledger(self.settings.ledger_db) as ledger:
                    row = ledger._conn.execute(
                        "SELECT MAX(id) as max_id FROM events"
                    ).fetchone()
                    cursor = int(row["max_id"] or 0) if row else 0
            except Exception:
                cursor = 0
            self._write_alert_cursor(cursor)
            return cursor
        try:
            return int(json.loads(p.read_text(encoding="utf-8")).get("cursor", 0))
        except (OSError, json.JSONDecodeError, ValueError):
            return 0

    def _write_alert_cursor(self, cursor: int) -> None:
        p = self._alert_cursor_path()
        p.write_text(json.dumps({"cursor": cursor}), encoding="utf-8")

    # -- logging --

    def _log(self, action: str, **payload: Any) -> None:
        append_jsonl(
            self.settings.log_jsonl,
            {"timestamp": utcnow(), "component": "telegram_bot", "action": action, **payload},
        )


def build_bot(settings: Settings) -> TelegramBot:
    """Build a fully-wired TelegramBot from settings."""
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "HERMES_OPERATOR_TELEGRAM_TOKEN not set. "
            "Create a bot via @BotFather and put the token in .env."
        )

    import os
    backend = create_backend(
        base_url=os.environ.get("HERMES_OPERATOR_BASE_URL", "") or "http://localhost:11434",
        api_key=os.environ.get("HERMES_OPERATOR_API_KEY", ""),
        model=os.environ.get("HERMES_OPERATOR_MODEL", "") or "qwen3:1.7b",
        timeout_seconds=180,
    )
    client = TelegramClient(
        token=settings.telegram_bot_token,
        timeout_seconds=settings.telegram_poll_timeout + 10,
    )
    return TelegramBot(settings=settings, backend=backend, client=client)
