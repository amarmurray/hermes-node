"""Tests for operator session persistence."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.session import (
    append_message,
    append_note,
    build_messages_for_backend,
    create_session,
    list_sessions,
    read_context,
    read_notes,
    read_thread,
    session_dir,
    write_notes,
)


class SessionCreationTests(unittest.TestCase):
    def test_create_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root, model="test-model")
            self.assertTrue(sid.startswith("session_"))
            sdir = session_dir(root, sid)
            self.assertTrue(sdir.exists())
            self.assertTrue((sdir / "context.json").exists())
            self.assertTrue((sdir / "notes.md").exists())

    def test_create_session_writes_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root, model="qwen3:1.7b")
            ctx = read_context(root, sid)
            self.assertEqual(ctx["session_id"], sid)
            self.assertEqual(ctx["model"], "qwen3:1.7b")
            self.assertIn("created_at", ctx)

    def test_resume_existing_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid1 = create_session(root)
            sid2 = create_session(root, resume=sid1)
            self.assertEqual(sid1, sid2)

    def test_resume_nonexistent_creates_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root, resume="session_fake_123")
            self.assertNotEqual(sid, "session_fake_123")
            self.assertTrue(sid.startswith("session_"))


class ThreadTests(unittest.TestCase):
    def test_append_and_read_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root)

            append_message(root, sid, role="user", content="Hello")
            append_message(root, sid, role="assistant", content="Hi there")

            thread = read_thread(root, sid)
            self.assertEqual(len(thread), 2)
            self.assertEqual(thread[0]["role"], "user")
            self.assertEqual(thread[0]["content"], "Hello")
            self.assertEqual(thread[1]["role"], "assistant")
            self.assertEqual(thread[1]["content"], "Hi there")

    def test_thread_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root)

            for i in range(20):
                append_message(root, sid, role="user", content=f"msg {i}")

            thread = read_thread(root, sid, limit=5)
            self.assertEqual(len(thread), 5)
            self.assertEqual(thread[0]["content"], "msg 15")

    def test_tool_call_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root)

            tc = [{"id": "call_1", "type": "function", "function": {"name": "test", "arguments": "{}"}}]
            append_message(root, sid, role="assistant", content="", tool_calls=tc)
            append_message(root, sid, role="tool", content='{"result": 42}', tool_call_id="call_1", name="test")

            thread = read_thread(root, sid)
            self.assertEqual(len(thread), 2)
            self.assertEqual(thread[0]["tool_calls"], tc)
            self.assertEqual(thread[1]["tool_call_id"], "call_1")

    def test_empty_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root)
            thread = read_thread(root, sid)
            self.assertEqual(thread, [])


class NotesTests(unittest.TestCase):
    def test_read_initial_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root)
            notes = read_notes(root, sid)
            self.assertIn(sid, notes)

    def test_write_and_read_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root)
            write_notes(root, sid, "Custom notes content")
            notes = read_notes(root, sid)
            self.assertEqual(notes, "Custom notes content")

    def test_append_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root)
            append_note(root, sid, "First note")
            append_note(root, sid, "Second note")
            notes = read_notes(root, sid)
            self.assertIn("First note", notes)
            self.assertIn("Second note", notes)


class BuildMessagesTests(unittest.TestCase):
    def test_build_includes_system_and_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root)
            append_message(root, sid, role="user", content="Hello")
            append_message(root, sid, role="assistant", content="Hi")

            messages = build_messages_for_backend(
                root, sid,
                system_prompt="You are helpful.",
            )
            self.assertEqual(messages[0]["role"], "system")
            self.assertEqual(messages[0]["content"], "You are helpful.")
            self.assertEqual(messages[1]["role"], "user")
            self.assertEqual(messages[2]["role"], "assistant")

    def test_build_respects_context_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root)
            for i in range(30):
                append_message(root, sid, role="user", content=f"msg {i}")

            messages = build_messages_for_backend(
                root, sid,
                system_prompt="system",
                context_window=5,
            )
            # system + 5 messages
            self.assertEqual(len(messages), 6)


class ListSessionsTests(unittest.TestCase):
    def test_list_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid1 = create_session(root, model="model-a")
            sid2 = create_session(root, model="model-b")

            sessions = list_sessions(root)
            ids = {s["session_id"] for s in sessions}
            self.assertIn(sid1, ids)
            self.assertIn(sid2, ids)

    def test_list_sessions_includes_message_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sid = create_session(root)
            append_message(root, sid, role="user", content="msg 1")
            append_message(root, sid, role="assistant", content="msg 2")

            sessions = list_sessions(root)
            matched = [s for s in sessions if s["session_id"] == sid]
            self.assertEqual(len(matched), 1)
            self.assertEqual(matched[0]["message_count"], 2)


if __name__ == "__main__":
    unittest.main()
