"""Tests for operator intent inference."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.backend_policy import OperatorIntent
from hermes_node_ops.operator.intent import IntentInference, infer_intent


class InferIntentTests(unittest.TestCase):
    def test_empty_input_defaults_to_chat(self) -> None:
        self.assertEqual(infer_intent("").intent, OperatorIntent.CHAT)
        self.assertEqual(infer_intent("   ").intent, OperatorIntent.CHAT)
        self.assertEqual(infer_intent(None).intent, OperatorIntent.CHAT)  # type: ignore[arg-type]

    def test_trivial_chat_defaults_to_chat(self) -> None:
        self.assertEqual(infer_intent("hello").intent, OperatorIntent.CHAT)
        self.assertEqual(
            infer_intent("what is the current queue state?").intent,
            OperatorIntent.CHAT,
        )

    def test_repo_diagnostic_triggers_escalation(self) -> None:
        for prompt in (
            "diagnose why task_123 failed",
            "debug the crash in ixion",
            "propose a patch for the ledger bug",
            "root cause the intermittent timeout",
            "why did the last run fail",
            "inspect the repo for stale imports",
            "assess repo health before I ship",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(
                    infer_intent(prompt).intent,
                    OperatorIntent.REPO_DIAGNOSTIC,
                    prompt,
                )

    def test_plan_markers_trigger_plan(self) -> None:
        for prompt in (
            "plan this migration end-to-end",
            "write a plan for the bakeoff refactor",
            "break down the specialist hardening work",
            "decompose the temporal cutover",
            "draft a plan for next week",
            "think through the escalation policy",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(infer_intent(prompt).intent, OperatorIntent.PLAN)

    def test_multi_ask_triggers_tool_dense(self) -> None:
        for prompt in (
            "check the queue and the last run receipt",
            "inspect foo, bar, and baz",
            "tell me about the branch, the changed files, and the last task",
            "a few things: queue, branch, recent runs",
            "both the plan and the todo",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(
                    infer_intent(prompt).intent,
                    OperatorIntent.TOOL_DENSE,
                    prompt,
                )

    def test_approval_brief_markers(self) -> None:
        for prompt in (
            "should I approve task_abc?",
            "give me a one-line verdict",
            "tldr recommendation for this patch",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(
                    infer_intent(prompt).intent,
                    OperatorIntent.APPROVAL_BRIEF,
                    prompt,
                )

    def test_priority_repo_diagnostic_over_plan(self) -> None:
        # Both markers present — repo diagnostic wins because it's first.
        result = infer_intent("diagnose the failure and write a plan to fix it")
        self.assertEqual(result.intent, OperatorIntent.REPO_DIAGNOSTIC)

    def test_signal_is_populated(self) -> None:
        result = infer_intent("diagnose why task_x failed")
        self.assertIsInstance(result, IntentInference)
        self.assertIn("repo_diagnostic", result.signal)
        self.assertTrue(result.signal)

    def test_to_dict_shape(self) -> None:
        result = infer_intent("hello")
        d = result.to_dict()
        self.assertEqual(set(d.keys()), {"intent", "signal"})
        self.assertEqual(d["intent"], "chat")

    def test_does_not_raise_on_weird_input(self) -> None:
        # Pathological inputs should never raise — they should default to CHAT.
        for weird in ("\x00\x01", "🔥🔥🔥", "a" * 10000, "???", "..."):
            with self.subTest(weird=weird):
                result = infer_intent(weird)
                self.assertIsInstance(result, IntentInference)


if __name__ == "__main__":
    unittest.main()
