"""Tests for operator tool dispatch and hermes-node integration."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.config import Settings
from hermes_node_ops.ledger import Ledger
from hermes_node_ops.models import TaskSubmission
from hermes_node_ops.operator.tools import TOOL_DEFINITIONS, ToolExecutor, get_tool_definitions


def _make_settings() -> Settings:
    tmpdir = tempfile.mkdtemp()
    root = Path(tmpdir)
    (root / ".env").write_text("", encoding="utf-8")
    return Settings.load(root)


class ToolDefinitionsTests(unittest.TestCase):
    def test_all_tools_have_names(self) -> None:
        defs = get_tool_definitions()
        self.assertGreater(len(defs), 0)
        for td in defs:
            self.assertIn("function", td)
            self.assertIn("name", td["function"])
            self.assertIn("parameters", td["function"])

    def test_tool_names_are_unique(self) -> None:
        defs = get_tool_definitions()
        names = [td["function"]["name"] for td in defs]
        self.assertEqual(len(names), len(set(names)))

    def test_expected_tools_present(self) -> None:
        defs = get_tool_definitions()
        names = {td["function"]["name"] for td in defs}
        expected = {
            "get_queue_status", "get_task_status", "submit_task",
            "approve_task", "list_recent_tasks", "list_recent_runs",
            "inspect_workspace", "read_run_receipt", "get_routing_policy",
            "repo_ls", "repo_read_file", "repo_grep", "git_status",
            "save_note",
        }
        self.assertTrue(expected.issubset(names), f"Missing tools: {expected - names}")

    def test_default_tool_definitions_are_compact(self) -> None:
        compact_defs = get_tool_definitions()
        verbose_defs = TOOL_DEFINITIONS
        self.assertLess(len(json.dumps(compact_defs)), len(json.dumps(verbose_defs)))
        for td in compact_defs:
            self.assertIn("function", td)
            self.assertIn("name", td["function"])
            self.assertIn("parameters", td["function"])


class ToolDispatchTests(unittest.TestCase):
    def test_unknown_tool_returns_error(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("nonexistent_tool", {})
        data = json.loads(result)
        self.assertIn("error", data)

    def test_get_queue_status(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("get_queue_status", {"limit": 3})
        data = json.loads(result)
        self.assertIn("counts", data)

    def test_get_queue_status_omits_heavy_run_payloads(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with Ledger(settings.ledger_db) as ledger:
            task = ledger.create_task(TaskSubmission(kind="summary", prompt="Test prompt from operator"))
            _, run = ledger.begin_run(
                task["id"],
                command=["echo", "hello"],
                working_dir=str(settings.repo_root),
                provider="openrouter",
                model="openai/gpt-oss-20b:free",
                max_turns=2,
                executor="hermes_chat",
            )
            ledger.finish_run(
                run["id"],
                exit_code=0,
                stdout="x" * 1000,
                stderr="y" * 1000,
                duration_ms=10,
            )

        result = executor.dispatch("get_queue_status", {"limit": 3})
        data = json.loads(result)
        self.assertGreaterEqual(len(data["recent_runs"]), 1)
        run_view = data["recent_runs"][0]
        self.assertIn("stdout_preview", run_view)
        self.assertIn("stderr_preview", run_view)
        self.assertNotIn("stdout", run_view)
        self.assertNotIn("stderr", run_view)
        self.assertNotIn("command", run_view)

    def test_submit_task_and_get_status(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)

        # Submit
        result = executor.dispatch("submit_task", {
            "kind": "summary",
            "prompt": "Test prompt from operator",
        })
        data = json.loads(result)
        self.assertTrue(data["submitted"])
        task_id = data["task_id"]
        self.assertEqual(data["kind"], "summary")
        self.assertFalse(data["approval_required"])

        # Get status
        result = executor.dispatch("get_task_status", {"task_id": task_id})
        data = json.loads(result)
        self.assertEqual(data["task"]["id"], task_id)
        self.assertEqual(data["task"]["state"], "pending")

    def test_submit_patch_requires_approval(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as repo_tmp:
            (Path(repo_tmp) / ".git").mkdir()
            result = executor.dispatch("submit_task", {
                "kind": "repo_patch_branch",
                "prompt": "Fix the bug",
                "repo_path": repo_tmp,
            })
        data = json.loads(result)
        self.assertTrue(data["submitted"])
        self.assertTrue(data["approval_required"])
        self.assertEqual(data["state"], "pending_approval")

    def test_approve_task(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as repo_tmp:
            (Path(repo_tmp) / ".git").mkdir()
            result = executor.dispatch("submit_task", {
                "kind": "repo_patch_branch",
                "prompt": "Fix the bug",
                "repo_path": repo_tmp,
            })
            task_id = json.loads(result)["task_id"]

            # Approve
            result = executor.dispatch("approve_task", {
                "task_id": task_id,
                "note": "Human confirmed",
            })
            data = json.loads(result)
            self.assertTrue(data["approved"])
            self.assertEqual(data["new_state"], "pending")

    def test_approve_non_pending_task_returns_error(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("submit_task", {
            "kind": "summary",
            "prompt": "Test",
        })
        task_id = json.loads(result)["task_id"]

        # Task is already in 'pending' (not pending_approval), approval should fail
        result = executor.dispatch("approve_task", {"task_id": task_id})
        data = json.loads(result)
        self.assertFalse(data["approved"])
        self.assertIn("error", data)

    def test_list_recent_tasks(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        executor.dispatch("submit_task", {"kind": "summary", "prompt": "one"})
        executor.dispatch("submit_task", {"kind": "summary", "prompt": "two"})

        result = executor.dispatch("list_recent_tasks", {"limit": 5})
        data = json.loads(result)
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 2)

    def test_get_routing_policy(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("get_routing_policy", {})
        data = json.loads(result)
        self.assertIn("routes", data)
        self.assertIn("available_executors", data)

    def test_inspect_workspace_nonexistent(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("inspect_workspace", {"task_id": "task_fake"})
        data = json.loads(result)
        self.assertFalse(data["exists"])


class RepoToolTests(unittest.TestCase):
    def test_repo_ls(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "hello.txt").write_text("hi", encoding="utf-8")
            result = executor.dispatch("repo_ls", {"path": tmpdir})
            data = json.loads(result)
            self.assertGreater(data["count"], 0)

    def test_repo_read_file(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "test.py"
            f.write_text("print('hello')\n", encoding="utf-8")
            result = executor.dispatch("repo_read_file", {"path": str(f)})
            data = json.loads(result)
            self.assertIn("print('hello')", data["content"])
            self.assertFalse(data["truncated"])

    def test_repo_read_file_nonexistent(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("repo_read_file", {"path": "/nonexistent/file.py"})
        data = json.loads(result)
        self.assertIn("error", data)

    def test_repo_grep(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "code.py"
            f.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
            result = executor.dispatch("repo_grep", {"pattern": "hello", "path": tmpdir})
            data = json.loads(result)
            self.assertGreater(data["count"], 0)

    def test_git_status_non_repo(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = executor.dispatch("git_status", {"repo_path": tmpdir})
            data = json.loads(result)
            # Should still return something (maybe empty branch)
            self.assertIn("repo_path", data)


if __name__ == "__main__":
    unittest.main()
