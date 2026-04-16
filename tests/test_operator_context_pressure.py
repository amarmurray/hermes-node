"""Tests for the context pressure estimation helper."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.runtime import _estimate_context_pressure  # noqa: E402


class ContextPressureTests(unittest.TestCase):
    def test_short_prompt_is_low(self) -> None:
        level, ratio = _estimate_context_pressure(
            "You are a helpful assistant.",
            [{"role": "user", "content": "Hello"}],
        )
        self.assertEqual(level, "low")
        self.assertLess(ratio, 0.5)

    def test_large_prompt_is_high(self) -> None:
        big_prompt = "x" * 50_000
        level, ratio = _estimate_context_pressure(big_prompt, [])
        self.assertEqual(level, "high")
        self.assertGreater(ratio, 0.8)

    def test_empty_messages_is_low(self) -> None:
        level, ratio = _estimate_context_pressure("short", [])
        self.assertEqual(level, "low")
        self.assertLess(ratio, 0.5)

    def test_ratio_calculation(self) -> None:
        # 1600 chars of system prompt + 0 messages = 400 tokens / 4096 budget
        prompt = "a" * 1600
        level, ratio = _estimate_context_pressure(prompt, [], token_budget=4096)
        expected_ratio = (1600 / 4) / 4096
        self.assertAlmostEqual(ratio, expected_ratio, places=4)

    def test_medium_pressure(self) -> None:
        # Budget 4096 tokens, need ratio > 0.5 => need > 2048 tokens => > 8192 chars
        prompt = "b" * 9000
        level, ratio = _estimate_context_pressure(prompt, [], token_budget=4096)
        self.assertEqual(level, "medium")

    def test_tool_calls_counted(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "test", "arguments": "x" * 4000}},
                ],
            }
        ]
        level, ratio = _estimate_context_pressure("short", messages, token_budget=4096)
        # 4000 chars of tool args alone = 1000 tokens / 4096
        self.assertGreater(ratio, 0.2)

    def test_zero_budget_returns_zero_ratio(self) -> None:
        level, ratio = _estimate_context_pressure("hello", [], token_budget=0)
        self.assertEqual(ratio, 0)
        self.assertEqual(level, "low")


if __name__ == "__main__":
    unittest.main()
