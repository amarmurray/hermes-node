"""Tests for the natural-language response synthesizer."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.response_synth import (
    MODE_SOFT_CAPS,
    ToolResultRef,
    synthesize_assistant_text,
)


class ResponseSynthTests(unittest.TestCase):
    def test_raw_mode_unchanged(self) -> None:
        original = "<think>plan</think>\n\nHello.\n\n\n\nWorld"
        out = synthesize_assistant_text(original, mode="raw")
        self.assertEqual(out.text, original)
        self.assertEqual(out.notes, ("raw mode",))

    def test_strips_think_blocks(self) -> None:
        text = "<think>secret reasoning</think>The repo is on main and clean."
        out = synthesize_assistant_text(text)
        self.assertNotIn("secret reasoning", out.text)
        self.assertIn("clean", out.text)
        self.assertIn("stripped think blocks", out.notes)

    def test_strips_slash_think_tokens(self) -> None:
        out = synthesize_assistant_text("ALIVE /think\nThe queue is quiet.")
        self.assertNotIn("/think", out.text)
        self.assertIn("ALIVE", out.text)
        self.assertIn("stripped slash-think tokens", out.notes)

    def test_strips_canned_preamble(self) -> None:
        text = "Sure, let me check that for you.\n\nThe registry shows two repos."
        out = synthesize_assistant_text(text)
        self.assertNotIn("Sure, let me", out.text)
        self.assertIn("registry shows", out.text)

    def test_collapses_duplicate_headings(self) -> None:
        text = "## Summary\n## Summary\nrepo is clean"
        out = synthesize_assistant_text(text)
        self.assertEqual(out.text.count("## Summary"), 1)

    def test_bare_json_collapses_to_human_sentence(self) -> None:
        out = synthesize_assistant_text(
            '{"submitted": true, "task_id": "t-1", "state": "pending_approval"}'
        )
        self.assertIn("Submitted task", out.text)
        self.assertIn("t-1", out.text)
        self.assertIn("pending_approval", out.text)

    def test_bare_json_error_path(self) -> None:
        out = synthesize_assistant_text(
            '{"written": false, "error": "branch policy refused main"}'
        )
        self.assertIn("refused", out.text.lower())
        self.assertIn("branch policy", out.text)

    def test_bare_json_grounded_false(self) -> None:
        out = synthesize_assistant_text(
            '{"grounded": false, "error": "no runs recorded"}'
        )
        self.assertIn("grounded", out.text.lower())
        self.assertIn("no runs", out.text)

    def test_bare_json_counts_summary(self) -> None:
        out = synthesize_assistant_text(
            '{"counts": {"present": 2, "absent": 0, "future": 1}}'
        )
        self.assertIn("2 present", out.text)
        self.assertIn("0 absent", out.text)
        self.assertIn("1 future-slot", out.text)

    def test_empty_with_tool_refs_uses_grounded_fallback(self) -> None:
        out = synthesize_assistant_text(
            "",
            tool_refs=[ToolResultRef(name="grounded_repo_summary")],
        )
        self.assertIn("grounded_repo_summary", out.text)
        self.assertIn("transcript", out.text)

    def test_empty_with_no_refs_short_default(self) -> None:
        out = synthesize_assistant_text("")
        self.assertIn("don't have anything", out.text)

    def test_appends_gloss_when_tool_unmentioned(self) -> None:
        out = synthesize_assistant_text(
            "Looks fine to me.",
            tool_refs=[
                ToolResultRef(
                    name="grounded_repo_summary",
                    payload_preview="ixion clean on main",
                ),
            ],
        )
        self.assertIn("Grounding:", out.text)
        self.assertIn("grounded repo summary", out.text)
        self.assertIn("ixion clean", out.text)

    def test_does_not_append_gloss_when_already_mentioned(self) -> None:
        out = synthesize_assistant_text(
            "I called grounded_repo_summary and it came back clean.",
            tool_refs=[ToolResultRef(name="grounded_repo_summary")],
        )
        self.assertNotIn("grounding:", out.text.lower())

    def test_brief_mode_soft_cap(self) -> None:
        # Use newlines so the soft truncator has paragraph boundaries and
        # safely cuts above the cap.
        long_text = "\n\n".join(["This is a paragraph."] * 200)
        out = synthesize_assistant_text(long_text, mode="brief")
        self.assertLessEqual(len(out.text), MODE_SOFT_CAPS["brief"] + 100)
        self.assertIn("trimmed", out.text)

    def test_full_mode_higher_cap(self) -> None:
        text = "x " * 100
        out = synthesize_assistant_text(text, mode="full")
        self.assertEqual(out.text.strip(), text.strip())

    def test_unknown_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            synthesize_assistant_text("hi", mode="garbled")

    def test_label_noise_stripped(self) -> None:
        out = synthesize_assistant_text("Summary: ixion is clean.\nResult: ok")
        self.assertNotIn("Summary:", out.text)
        self.assertNotIn("Result:", out.text)
        self.assertIn("ixion is clean", out.text)

    # Phase 5.1 — narration / tool-call leakage stripping

    def test_strips_i_would_call_preamble(self) -> None:
        text = "I would call get_queue_status to check.\n\nThe queue has 3 tasks."
        out = synthesize_assistant_text(text)
        self.assertNotIn("I would call", out.text)
        self.assertIn("queue has 3", out.text)

    def test_strips_first_i_will_preamble(self) -> None:
        text = "First, I'll check the ledger.\n\nNo active cooldowns."
        out = synthesize_assistant_text(text)
        self.assertNotIn("First, I'll", out.text)
        self.assertIn("No active cooldowns", out.text)

    def test_strips_im_going_to_preamble(self) -> None:
        text = "I'm going to check the repo state.\n\nIxion is on main, clean."
        out = synthesize_assistant_text(text)
        self.assertNotIn("I'm going to", out.text)
        self.assertIn("Ixion is on main", out.text)

    def test_strips_step_number_preamble(self) -> None:
        text = "Step 1: inspect the queue status\n\nThe queue is empty."
        out = synthesize_assistant_text(text)
        self.assertNotIn("Step 1", out.text)
        self.assertIn("queue is empty", out.text)

    def test_strips_tool_call_leakage_mid_text(self) -> None:
        text = "The queue is quiet.\nfunction_call: get_queue_status(limit=5)\nAll clear."
        out = synthesize_assistant_text(text)
        self.assertNotIn("function_call", out.text)
        self.assertNotIn("get_queue_status", out.text)
        self.assertIn("queue is quiet", out.text)
        self.assertIn("All clear", out.text)

    def test_strips_mid_text_narration_line(self) -> None:
        text = "Queue is empty.\nI'll now call list_registered_repos to check.\nTwo repos registered."
        out = synthesize_assistant_text(text)
        self.assertNotIn("I'll now call", out.text)
        self.assertIn("Queue is empty", out.text)
        self.assertIn("Two repos registered", out.text)

    def test_strips_next_i_will_narration(self) -> None:
        text = "Next, I will check the workspace.\n\nThe workspace has 3 files."
        out = synthesize_assistant_text(text)
        self.assertNotIn("Next, I will", out.text)
        self.assertIn("workspace has 3", out.text)

    def test_preserves_genuine_content_with_i_will(self) -> None:
        # "I will" in non-narration context should survive
        text = "I will need your approval before submitting the patch."
        out = synthesize_assistant_text(text)
        self.assertIn("approval", out.text)


if __name__ == "__main__":
    unittest.main()
