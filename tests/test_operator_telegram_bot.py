"""Tests for the TelegramBot ingress wedge.

Uses a fake Telegram client and a fake ChatBackend so we can exercise
allowlist gating, slash-command routing, session mapping, alert cursor
persistence, and offset persistence without touching the network or a
real LLM.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.config import Settings
from hermes_node_ops.ledger import Ledger
from hermes_node_ops.operator.backend import AssistantMessage
from hermes_node_ops.operator.telegram.bot import TelegramBot
from hermes_node_ops.operator.telegram.client import TelegramUpdate


@dataclass
class FakeClient:
    sent: list[tuple[int, str]] = field(default_factory=list)
    actions: list[tuple[int, str]] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "",
        reply_markup: dict[str, Any] | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        self.sent.append((chat_id, text))
        return {}

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        self.actions.append((chat_id, action))

    def answer_callback_query(self, callback_query_id: str, *, text: str = "", show_alert: bool = False) -> None:
        self.callbacks.append(callback_query_id)

    def get_updates(self, *, offset: int = 0, timeout: int = 30, allowed_updates: list[str] | None = None) -> list:
        return []


@dataclass
class FakeBackend:
    model: str = "fake-model"
    canned: list[AssistantMessage] = field(default_factory=list)

    @property
    def model_name(self) -> str:
        return self.model

    def available(self) -> bool:
        return True

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
    ) -> AssistantMessage:
        if self.canned:
            return self.canned.pop(0)
        return AssistantMessage(content="ok", tool_calls=[])


def _make_settings(
    *,
    allowed: tuple[int, ...] = (1001,),
    token: str = "fake-token",
) -> Settings:
    tmpdir = tempfile.mkdtemp()
    root = Path(tmpdir)
    env_content = [
        f"HERMES_OPERATOR_TELEGRAM_TOKEN={token}",
        "HERMES_OPERATOR_TELEGRAM_ALLOWED_CHAT_IDS=" + ",".join(str(i) for i in allowed),
    ]
    (root / ".env").write_text("\n".join(env_content) + "\n", encoding="utf-8")
    settings = Settings.load(root)
    settings.ensure_wrapper_dirs()
    # Initialize the ledger schema so ToolExecutor dispatch works
    with Ledger(settings.ledger_db):
        pass
    return settings


def _message_update(chat_id: int, text: str, *, update_id: int = 1) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=update_id,
        message_id=1,
        chat_id=chat_id,
        user_id=chat_id,
        username="tester",
        text=text,
    )


class AllowlistTests(unittest.TestCase):
    def test_empty_allowlist_rejects_all(self) -> None:
        settings = _make_settings(allowed=())
        client = FakeClient()
        bot = TelegramBot(settings, FakeBackend(), client)  # type: ignore[arg-type]
        bot._handle_update(_message_update(999, "/status"))
        # Empty allowlist must silently drop (no outbound message to unknown user)
        self.assertEqual(client.sent, [])

    def test_denied_chat_gets_unauthorized_notice(self) -> None:
        settings = _make_settings(allowed=(1001,))
        client = FakeClient()
        bot = TelegramBot(settings, FakeBackend(), client)  # type: ignore[arg-type]
        bot._handle_update(_message_update(2002, "/status"))
        self.assertEqual(len(client.sent), 1)
        _, text = client.sent[0]
        self.assertIn("unauthorized", text)

    def test_allowed_chat_is_permitted(self) -> None:
        settings = _make_settings(allowed=(1001,))
        client = FakeClient()
        bot = TelegramBot(settings, FakeBackend(), client)  # type: ignore[arg-type]
        bot._handle_update(_message_update(1001, "/help"))
        self.assertEqual(len(client.sent), 1)
        chat_id, text = client.sent[0]
        self.assertEqual(chat_id, 1001)
        self.assertIn("/status", text)


class SlashCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = _make_settings(allowed=(1001,))
        self.client = FakeClient()
        self.bot = TelegramBot(self.settings, FakeBackend(), self.client)  # type: ignore[arg-type]

    def test_start_shows_welcome(self) -> None:
        self.bot._handle_update(_message_update(1001, "/start"))
        self.assertIn("hermes-node operator ready", self.client.sent[-1][1])

    def test_botname_suffix_is_stripped(self) -> None:
        self.bot._handle_update(_message_update(1001, "/help@hermes_operator_bot"))
        self.assertIn("/status", self.client.sent[-1][1])

    def test_session_command_reports_id(self) -> None:
        self.bot._handle_update(_message_update(1001, "/session"))
        text = self.client.sent[-1][1]
        self.assertTrue(text.startswith("session: session_"))

    def test_new_rotates_session(self) -> None:
        self.bot._handle_update(_message_update(1001, "/session"))
        first = self.client.sent[-1][1]
        self.bot._handle_update(_message_update(1001, "/new"))
        self.bot._handle_update(_message_update(1001, "/session"))
        second = self.client.sent[-1][1]
        self.assertNotEqual(first, second)

    def test_status_renders_empty_queue(self) -> None:
        self.bot._handle_update(_message_update(1001, "/status"))
        text = self.client.sent[-1][1]
        self.assertIn("queue:", text)

    def test_approve_without_arg_reports_usage(self) -> None:
        self.bot._handle_update(_message_update(1001, "/approve"))
        self.assertIn("usage:", self.client.sent[-1][1])

    def test_unknown_command(self) -> None:
        self.bot._handle_update(_message_update(1001, "/banana"))
        self.assertIn("unknown command", self.client.sent[-1][1])


class SessionMappingTests(unittest.TestCase):
    def test_session_marker_persists_per_chat(self) -> None:
        settings = _make_settings(allowed=(1001, 1002))
        client = FakeClient()
        bot = TelegramBot(settings, FakeBackend(), client)  # type: ignore[arg-type]

        sid_a1 = bot._session_id_for_chat(1001)
        sid_a2 = bot._session_id_for_chat(1001)
        sid_b1 = bot._session_id_for_chat(1002)

        self.assertEqual(sid_a1, sid_a2)
        self.assertNotEqual(sid_a1, sid_b1)
        self.assertTrue((settings.ledger_db.parent / "telegram_session_1001.txt").exists())
        self.assertTrue((settings.ledger_db.parent / "telegram_session_1002.txt").exists())

    def test_rotate_session_creates_new_id(self) -> None:
        settings = _make_settings(allowed=(1001,))
        bot = TelegramBot(settings, FakeBackend(), FakeClient())  # type: ignore[arg-type]
        first = bot._session_id_for_chat(1001)
        bot._rotate_session(1001)
        second = bot._session_id_for_chat(1001)
        self.assertNotEqual(first, second)


class PersistenceTests(unittest.TestCase):
    def test_offset_roundtrip(self) -> None:
        settings = _make_settings()
        bot = TelegramBot(settings, FakeBackend(), FakeClient())  # type: ignore[arg-type]
        self.assertEqual(bot._read_offset(), 0)
        bot._write_offset(1234)
        self.assertEqual(bot._read_offset(), 1234)

        data = json.loads(bot._offset_path().read_text(encoding="utf-8"))
        self.assertEqual(data["offset"], 1234)

    def test_alert_cursor_initializes_from_max_event_id(self) -> None:
        settings = _make_settings()
        # Insert a dummy event so MAX(events.id) > 0.
        # We disable FK enforcement for this probe rather than stand up a
        # whole task row — we only care that the cursor reflects MAX(id).
        with Ledger(settings.ledger_db) as ledger:
            ledger._conn.execute("PRAGMA foreign_keys = OFF")
            ledger._conn.execute(
                "INSERT INTO events (task_id, created_at, event_type, level, message, data_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("task_x", "2026-04-14T00:00:00Z", "task.queued", "info", "queued", "{}"),
            )
            ledger._conn.commit()

        bot = TelegramBot(settings, FakeBackend(), FakeClient())  # type: ignore[arg-type]
        cursor = bot._read_alert_cursor()
        self.assertGreaterEqual(cursor, 1)
        # Second read loads from file, not from DB
        bot2 = TelegramBot(settings, FakeBackend(), FakeClient())  # type: ignore[arg-type]
        self.assertEqual(bot2._read_alert_cursor(), cursor)


class CallbackTests(unittest.TestCase):
    def test_callback_query_is_acknowledged(self) -> None:
        settings = _make_settings(allowed=(1001,))
        client = FakeClient()
        bot = TelegramBot(settings, FakeBackend(), client)  # type: ignore[arg-type]
        update = TelegramUpdate(
            update_id=5,
            message_id=10,
            chat_id=1001,
            user_id=1001,
            username="tester",
            text="",
            is_callback=True,
            callback_data="approve:task_missing",
            callback_query_id="cb-1",
        )
        bot._handle_update(update)
        self.assertEqual(client.callbacks, ["cb-1"])
        # approve should fail on a missing task but still respond
        self.assertTrue(any("not approved" in t or "error" in t for _, t in client.sent))


if __name__ == "__main__":
    unittest.main()
