"""Tests for operator backend abstraction."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.backend import (
    AssistantMessage,
    OpenAICompatibleBackend,
    ToolCall,
    create_backend,
)


class BackendCreationTests(unittest.TestCase):
    def test_create_with_defaults(self) -> None:
        backend = create_backend()
        self.assertEqual(backend.base_url, "http://localhost:11434")
        self.assertEqual(backend.api_key, "")
        self.assertEqual(backend.model, "")

    def test_create_with_params(self) -> None:
        backend = create_backend(
            base_url="http://localhost:8080",
            model="test-model",
            api_key="sk-test",
        )
        self.assertEqual(backend.base_url, "http://localhost:8080")
        self.assertEqual(backend.model, "test-model")
        self.assertEqual(backend.api_key, "sk-test")

    def test_model_name_property(self) -> None:
        backend = create_backend(model="qwen3:1.7b")
        self.assertEqual(backend.model_name, "qwen3:1.7b")

    def test_model_name_unknown_when_empty(self) -> None:
        backend = create_backend()
        self.assertEqual(backend.model_name, "unknown")


class ResponseParsingTests(unittest.TestCase):
    def test_parse_text_response(self) -> None:
        backend = OpenAICompatibleBackend(base_url="http://localhost", model="test")
        data = {
            "choices": [{
                "message": {"content": "Hello, I'm the operator."},
                "finish_reason": "stop",
            }],
        }
        msg = backend._parse_response(data)
        self.assertEqual(msg.content, "Hello, I'm the operator.")
        self.assertEqual(msg.finish_reason, "stop")
        self.assertEqual(msg.tool_calls, [])

    def test_parse_tool_call_response(self) -> None:
        backend = OpenAICompatibleBackend(base_url="http://localhost", model="test")
        data = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "get_queue_status",
                            "arguments": '{"limit": 5}',
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        msg = backend._parse_response(data)
        self.assertEqual(msg.content, "")
        self.assertEqual(len(msg.tool_calls), 1)
        tc = msg.tool_calls[0]
        self.assertEqual(tc.id, "call_abc123")
        self.assertEqual(tc.name, "get_queue_status")
        self.assertEqual(tc.arguments, {"limit": 5})

    def test_parse_empty_choices(self) -> None:
        backend = OpenAICompatibleBackend(base_url="http://localhost", model="test")
        msg = backend._parse_response({"choices": []})
        self.assertEqual(msg.content, "")
        self.assertEqual(msg.tool_calls, [])

    def test_parse_malformed_tool_arguments(self) -> None:
        backend = OpenAICompatibleBackend(base_url="http://localhost", model="test")
        data = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "test",
                            "arguments": "not valid json",
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        msg = backend._parse_response(data)
        self.assertEqual(len(msg.tool_calls), 1)
        self.assertIn("_raw", msg.tool_calls[0].arguments)

    def test_parse_multiple_tool_calls(self) -> None:
        backend = OpenAICompatibleBackend(base_url="http://localhost", model="test")
        data = {
            "choices": [{
                "message": {
                    "content": "Let me check both.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_queue_status", "arguments": "{}"},
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": "get_routing_policy", "arguments": "{}"},
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }],
        }
        msg = backend._parse_response(data)
        self.assertEqual(msg.content, "Let me check both.")
        self.assertEqual(len(msg.tool_calls), 2)
        self.assertEqual(msg.tool_calls[0].name, "get_queue_status")
        self.assertEqual(msg.tool_calls[1].name, "get_routing_policy")


class AvailabilityTests(unittest.TestCase):
    def test_unavailable_when_unreachable(self) -> None:
        backend = create_backend(base_url="http://localhost:99999")
        self.assertFalse(backend.available())


if __name__ == "__main__":
    unittest.main()
