"""Tests for the shared operator runtime."""

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
from hermes_node_ops.operator.backend import AssistantMessage, ToolCall
from hermes_node_ops.operator.runtime import run_operator_turn
from hermes_node_ops.operator.session import create_session, read_thread
from hermes_node_ops.operator.tools import ToolExecutor


@dataclass
class FakeBackend:
    canned: list[AssistantMessage] = field(default_factory=list)
    model: str = "fake-runtime"

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
        return AssistantMessage(content="ok")


@dataclass
class FakeExecutor:
    results: dict[str, str]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        return self.results[name]


def _make_settings() -> Settings:
    tmpdir = tempfile.mkdtemp()
    root = Path(tmpdir)
    (root / ".env").write_text("", encoding="utf-8")
    settings = Settings.load(root)
    settings.ensure_wrapper_dirs()
    with Ledger(settings.ledger_db):
        pass
    return settings


class OperatorRuntimeTests(unittest.TestCase):
    def test_runtime_synthesizes_natural_reply(self) -> None:
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="fake-runtime")
        backend = FakeBackend(
            canned=[
                AssistantMessage(
                    content="<think>hidden</think>Sure, let me check that.\n\nThe queue is empty.",
                )
            ]
        )
        executor = ToolExecutor(settings, settings.repo_root, session_id=sid)

        result = run_operator_turn(
            settings=settings,
            backend=backend,
            executor=executor,
            repo_root=settings.repo_root,
            session_id=sid,
            user_input="What is happening?",
        )

        self.assertEqual(result.text, "The queue is empty.")
        thread = read_thread(settings.repo_root, sid)
        self.assertEqual(thread[-1]["content"], "The queue is empty.")

    def test_runtime_uses_grounded_fallback_when_tool_has_no_writeup(self) -> None:
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="fake-runtime")
        backend = FakeBackend(
            canned=[
                AssistantMessage(
                    content="",
                    tool_calls=[ToolCall(id="tc1", name="get_queue_status", arguments={})],
                ),
                AssistantMessage(content=""),
            ]
        )
        executor = FakeExecutor(
            results={
                "get_queue_status": json.dumps({"counts": {"queued": 0, "running": 0}})
            }
        )

        result = run_operator_turn(
            settings=settings,
            backend=backend,
            executor=executor,  # type: ignore[arg-type]
            repo_root=settings.repo_root,
            session_id=sid,
            user_input="Check the queue.",
        )

        self.assertIn("get_queue_status", result.text)
        self.assertIn("session transcript", result.text)
        self.assertEqual(result.effects.tool_names, ["get_queue_status"])
        self.assertEqual(result.tool_trace[0].name, "get_queue_status")

    def test_runtime_tracks_plan_effects_via_real_tool_executor(self) -> None:
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="fake-runtime")
        backend = FakeBackend(
            canned=[
                AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="tc1",
                            name="write_plan",
                            arguments={"content": "# Plan\n\n1. Inspect queue"},
                        )
                    ],
                ),
                AssistantMessage(content="Plan is written."),
            ]
        )
        executor = ToolExecutor(settings, settings.repo_root, session_id=sid)

        result = run_operator_turn(
            settings=settings,
            backend=backend,
            executor=executor,
            repo_root=settings.repo_root,
            session_id=sid,
            user_input="Make a plan.",
        )

        self.assertTrue(result.effects.plan_touched)
        plan_path = settings.repo_root / "state" / "operator_sessions" / sid / "plan.md"
        self.assertTrue(plan_path.exists())
        self.assertIn("Inspect queue", plan_path.read_text(encoding="utf-8"))

    def test_runtime_tracks_created_task_and_approval_need_from_tool_payload(self) -> None:
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="fake-runtime")
        backend = FakeBackend(
            canned=[
                AssistantMessage(
                    content="",
                    tool_calls=[ToolCall(id="tc1", name="submit_task", arguments={})],
                ),
                AssistantMessage(content="Submitted for approval."),
            ]
        )
        executor = FakeExecutor(
            results={
                "submit_task": json.dumps(
                    {
                        "submitted": True,
                        "task_id": "task_123",
                        "state": "pending_approval",
                        "approval_required": True,
                    }
                )
            }
        )

        result = run_operator_turn(
            settings=settings,
            backend=backend,
            executor=executor,  # type: ignore[arg-type]
            repo_root=settings.repo_root,
            session_id=sid,
            user_input="Queue a risky task.",
        )

        self.assertEqual(result.effects.created_task_ids, ["task_123"])
        self.assertEqual(result.effects.task_ids_needing_approval, ["task_123"])
        self.assertTrue(result.effects.approval_needed)

    def test_action_completion_guard_nudges_then_completes(self) -> None:
        """Guard should inject a nudge when model narrates instead of acting,
        then the second call actually performs the work."""
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="fake-runtime")
        backend = FakeBackend(
            canned=[
                # Round 1: pure narration — no tool calls, no real content
                AssistantMessage(content="I would call get_queue_status to check this."),
                # Round 2 (after nudge): model actually calls the tool
                AssistantMessage(
                    content="",
                    tool_calls=[ToolCall(id="tc1", name="get_queue_status", arguments={})],
                ),
                # Round 3: model synthesizes
                AssistantMessage(content="The queue is currently empty."),
            ]
        )
        executor = FakeExecutor(
            results={"get_queue_status": json.dumps({"counts": {"queued": 0}})}
        )

        result = run_operator_turn(
            settings=settings,
            backend=backend,
            executor=executor,  # type: ignore[arg-type]
            repo_root=settings.repo_root,
            session_id=sid,
            user_input="show me the queue",
        )

        self.assertFalse(result.error)
        self.assertIn("The queue is currently empty.", result.text)
        self.assertIn("get_queue_status", result.effects.tool_names)
        # Must have taken more than 1 round (guard added extra round)
        self.assertGreater(result.tool_rounds, 1)

    def test_action_completion_guard_does_not_fire_when_real_content_follows(self) -> None:
        """Guard must NOT fire when narration opener is followed by a real answer."""
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="fake-runtime")
        backend = FakeBackend(
            canned=[
                # Narration opener + real answer — guard should stay silent
                AssistantMessage(
                    content="Let me check that.\n\nThe queue has no active tasks.",
                ),
            ]
        )
        executor = ToolExecutor(settings, settings.repo_root, session_id=sid)

        result = run_operator_turn(
            settings=settings,
            backend=backend,
            executor=executor,
            repo_root=settings.repo_root,
            session_id=sid,
            user_input="how is the queue?",
        )

        # Guard should not have fired — no extra round, no nudge
        self.assertEqual(result.tool_rounds, 1)
        self.assertIn("active tasks", result.text)
        self.assertFalse(result.error)


if __name__ == "__main__":
    unittest.main()
