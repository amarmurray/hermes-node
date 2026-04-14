"""Tests for the stdlib Telegram client."""

from __future__ import annotations

from hermes_node_ops.operator.telegram.client import (
    TelegramClient,
    TelegramUpdate,
    _chunk_text,
    parse_update,
)


def test_parse_update_message() -> None:
    raw = {
        "update_id": 42,
        "message": {
            "message_id": 7,
            "chat": {"id": 1001},
            "from": {"id": 2002, "username": "alice"},
            "text": "/status",
        },
    }
    upd = parse_update(raw)
    assert isinstance(upd, TelegramUpdate)
    assert upd.update_id == 42
    assert upd.chat_id == 1001
    assert upd.user_id == 2002
    assert upd.username == "alice"
    assert upd.text == "/status"
    assert upd.is_callback is False


def test_parse_update_edited_message_fallback() -> None:
    raw = {
        "update_id": 5,
        "edited_message": {
            "message_id": 3,
            "chat": {"id": 9},
            "from": {"id": 10},
            "text": "hi",
        },
    }
    upd = parse_update(raw)
    assert upd.chat_id == 9
    assert upd.text == "hi"
    assert upd.username == ""


def test_parse_update_callback_query() -> None:
    raw = {
        "update_id": 99,
        "callback_query": {
            "id": "cb-1",
            "data": "approve:task_abc",
            "from": {"id": 7, "username": "bob"},
            "message": {"message_id": 15, "chat": {"id": 500}},
        },
    }
    upd = parse_update(raw)
    assert upd.is_callback is True
    assert upd.callback_data == "approve:task_abc"
    assert upd.callback_query_id == "cb-1"
    assert upd.chat_id == 500
    assert upd.user_id == 7
    assert upd.text == ""


def test_parse_update_missing_fields() -> None:
    upd = parse_update({"update_id": 1})
    assert upd.update_id == 1
    assert upd.chat_id == 0
    assert upd.text == ""


def test_chunk_text_short_passthrough() -> None:
    assert _chunk_text("hello", 100) == ["hello"]


def test_chunk_text_splits_on_newline() -> None:
    text = "a" * 50 + "\n" + "b" * 50 + "\n" + "c" * 50
    chunks = _chunk_text(text, 70)
    assert len(chunks) >= 2
    joined = "\n".join(chunks).replace("\n\n", "\n")
    assert "a" * 50 in joined
    assert "c" * 50 in joined
    for c in chunks:
        assert len(c) <= 70


def test_chunk_text_hard_cut_when_no_newline() -> None:
    text = "x" * 150
    chunks = _chunk_text(text, 50)
    assert len(chunks) == 3
    for c in chunks:
        assert len(c) <= 50


def test_telegram_client_url_shape() -> None:
    client = TelegramClient(token="abc:123")
    assert client._url("getMe").endswith("/botabc:123/getMe")
