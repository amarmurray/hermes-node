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
from hermes_node_ops.jsonl import append_jsonl
from hermes_node_ops.models import TaskSubmission
from hermes_node_ops.wrapper import (
    CommandResult,
    build_task_command,
    clean_console_text,
    execute_task,
    extract_session_id,
    summarize_command_result,
)


class WrapperCommandTests(unittest.TestCase):
    def make_settings(self) -> Settings:
        tmpdir = tempfile.mkdtemp()
        root = Path(tmpdir)
        (root / ".env").write_text("", encoding="utf-8")
        settings = Settings.load(root)
        settings.default_provider = "openrouter"
        settings.default_model = "openai/gpt-oss-20b:free"
        return settings

    def test_build_summary_task_command_uses_safe_defaults(self) -> None:
        settings = self.make_settings()
        with Ledger(settings.ledger_db) as ledger:
            task = ledger.create_task(TaskSubmission(kind="summary", prompt="hello"))
            command, provider, model, max_turns, workdir = build_task_command(settings, task=task)
        self.assertEqual(command[1:4], ["chat", "-q", command[3]])
        self.assertIn("--provider", command)
        self.assertIn("openrouter", command)
        self.assertIn("--model", command)
        self.assertIn("openai/gpt-oss-20b:free", command)
        self.assertIn("-t", command)
        self.assertIn("clarify,memory,session_search", command)
        self.assertEqual(provider, "openrouter")
        self.assertEqual(model, "openai/gpt-oss-20b:free")
        self.assertEqual(max_turns, 2)
        self.assertEqual(workdir, str(settings.repo_root))

    def test_repo_read_command_uses_repo_path(self) -> None:
        settings = self.make_settings()
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo_path = Path(repo_tmp)
            (repo_path / ".git").mkdir()
            with Ledger(settings.ledger_db) as ledger:
                task = ledger.create_task(
                    TaskSubmission(kind="repo_read", prompt="inspect", repo_path=str(repo_path))
                )
                command, _, _, _, workdir = build_task_command(settings, task=task)
        self.assertIn("file,clarify,memory,session_search", command)
        self.assertIn("--worktree", command)
        self.assertEqual(workdir, str(repo_path))

    def test_execute_task_records_run_and_summary(self) -> None:
        settings = self.make_settings()

        def fake_runner(settings, command, **kwargs):  # type: ignore[no-untyped-def]
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="\n╭─ ⚕ Hermes ──────────────────╮\nALIVE\n\nsession_id: test_session\n",
                stderr="",
                duration_ms=25,
            )

        with Ledger(settings.ledger_db) as ledger:
            task = ledger.create_task(TaskSubmission(kind="summary", prompt="Reply with exactly: ALIVE"))
            outcome = execute_task(settings, ledger, task["id"], command_runner=fake_runner)
            summary = ledger.latest_summary(task_id=task["id"])
            run = ledger.latest_run_for_task(task["id"])

        self.assertEqual(outcome.state, "completed")
        self.assertEqual(outcome.summary, "ALIVE")
        self.assertEqual(summary["content"], "ALIVE")
        self.assertEqual(run["hermes_session_id"], "test_session")

    def test_summary_helpers_strip_banner_lines(self) -> None:
        raw = "\n╭─ ⚕ Hermes ──────────────────╮\nALIVE\nsession_id: abc123\n"
        self.assertEqual(clean_console_text(raw), "ALIVE")
        self.assertEqual(extract_session_id(raw), "abc123")
        self.assertEqual(
            summarize_command_result({"id": "task_1"}, {"stdout": raw, "stderr": "", "state": "completed", "exit_code": 0}),
            "ALIVE",
        )

    def test_append_jsonl_writes_single_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "log.jsonl"
            append_jsonl(path, {"action": "test", "ok": True})
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["action"], "test")


if __name__ == "__main__":
    unittest.main()
