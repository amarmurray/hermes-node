"""Tests for the operator Temporal seam."""

from __future__ import annotations

import os
import subprocess
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
from hermes_node_ops.operator.session import read_session_project
from hermes_node_ops.operator.temporal_seam import (
    OperatorInvocation,
    invoke_operator_once,
)


@dataclass
class FakeBackend:
    canned: list[AssistantMessage] = field(default_factory=list)
    model: str = "fake-temporal"

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


def _make_settings() -> Settings:
    tmpdir = tempfile.mkdtemp()
    root = Path(tmpdir)
    (root / ".env").write_text("", encoding="utf-8")
    settings = Settings.load(root)
    settings.ensure_wrapper_dirs()
    with Ledger(settings.ledger_db):
        pass
    return settings


def _init_git_repo(path: Path) -> None:
    result = subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    subprocess.run(
        ["git", "init"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "main"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )


class TemporalSeamTests(unittest.TestCase):
    def test_invoke_operator_once_returns_structured_outcome(self) -> None:
        settings = _make_settings()
        backend = FakeBackend(
            canned=[
                AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="tc1",
                            name="write_plan",
                            arguments={"content": "# Plan\n\n- inspect repo"},
                        )
                    ],
                ),
                AssistantMessage(content="Plan written and ready."),
            ]
        )

        outcome = invoke_operator_once(
            settings,
            backend,
            OperatorInvocation(
                goal="Inspect the repo and plan the next steps.",
                context="TreasuryOS operator dry-run",
            ),
        )

        self.assertTrue(outcome.session_id.startswith("session_"))
        self.assertEqual(outcome.backend_model, "fake-temporal")
        self.assertTrue(outcome.plan_touched)
        self.assertIn("Plan written", outcome.assistant_text)
        self.assertIn("write_plan", outcome.tool_names)

    def test_invoke_operator_once_stamps_session_project(self) -> None:
        settings = _make_settings()
        backend = FakeBackend(canned=[AssistantMessage(content="ok")])

        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "ixion"
            project_root.mkdir(parents=True, exist_ok=True)
            _init_git_repo(project_root)

            prev_cwd = os.getcwd()
            try:
                os.chdir(project_root)
                outcome = invoke_operator_once(
                    settings,
                    backend,
                    OperatorInvocation(goal="Say hi."),
                )
            finally:
                os.chdir(prev_cwd)

        project = read_session_project(settings.repo_root, outcome.session_id)
        self.assertEqual(project.get("repo_name"), "ixion")
        self.assertEqual(
            Path(project.get("repo_root", "")).resolve(),
            project_root.resolve(),
        )


if __name__ == "__main__":
    unittest.main()
