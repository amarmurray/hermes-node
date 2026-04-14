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
from hermes_node_ops.models import (
    TASK_STATE_DEFERRED_RATE_LIMIT,
    TASK_STATE_DONE,
    TASK_STATE_FAILED,
    TaskSubmission,
)
from hermes_node_ops.wrapper import (
    CommandResult,
    available_executors,
    build_task_command,
    clean_console_text,
    compute_backoff_seconds,
    execute_task,
    extract_session_id,
    is_rate_limit_error,
    summarize_command_result,
)
from hermes_node_ops.routing import EXECUTOR_HERMES_CHAT


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
        expected_stdout = "\n╭─ ⚕ Hermes ──────────────────╮\nALIVE\n\nsession_id: test_session\n"

        def fake_runner(settings, command, **kwargs):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=expected_stdout,
                stderr="",
                duration_ms=25,
            )

        with Ledger(settings.ledger_db) as ledger:
            task = ledger.create_task(TaskSubmission(kind="summary", prompt="Reply with exactly: ALIVE"))
            outcome = execute_task(settings, ledger, task["id"], command_runner=fake_runner)
            summary = ledger.latest_summary(task_id=task["id"])
            run = ledger.latest_run_for_task(task["id"])

        self.assertEqual(outcome.state, "done")
        self.assertEqual(outcome.summary, "ALIVE")
        self.assertEqual(summary["content"], "ALIVE")
        self.assertEqual(run["hermes_session_id"], "test_session")
        workspace = Path(task["workspace_dir"])
        self.assertTrue((workspace / "input.md").exists())
        self.assertEqual((workspace / "runs" / run["id"] / "stdout.txt").read_text(encoding="utf-8"), expected_stdout)
        receipt = json.loads((workspace / "runs" / run["id"] / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["executor"], EXECUTOR_HERMES_CHAT)
        self.assertEqual(receipt["task_state"], TASK_STATE_DONE)
        self.assertEqual(receipt["run_state"], TASK_STATE_DONE)

    def test_summary_helpers_strip_banner_lines(self) -> None:
        raw = "\n╭─ ⚕ Hermes ──────────────────╮\nALIVE\nsession_id: abc123\n"
        self.assertEqual(clean_console_text(raw), "ALIVE")
        self.assertEqual(extract_session_id(raw), "abc123")
        self.assertEqual(
            summarize_command_result(
                {"id": "task_1"},
                {"stdout": raw, "stderr": "", "state": "done", "exit_code": 0},
            ),
            "ALIVE",
        )

    def test_append_jsonl_writes_single_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "log.jsonl"
            append_jsonl(path, {"action": "test", "ok": True})
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["action"], "test")


class RateLimitDetectionTests(unittest.TestCase):
    def test_detect_429_in_stderr(self) -> None:
        self.assertTrue(is_rate_limit_error("", "HTTP 429: Provider returned error"))

    def test_detect_rate_limit_phrase(self) -> None:
        self.assertTrue(is_rate_limit_error("", "Rate limit exceeded, try again later"))

    def test_detect_too_many_requests(self) -> None:
        self.assertTrue(is_rate_limit_error("Too many requests", ""))

    def test_detect_provider_429(self) -> None:
        self.assertTrue(is_rate_limit_error("", "Provider returned error: 429"))

    def test_non_rate_limit_error(self) -> None:
        self.assertFalse(is_rate_limit_error("", "Connection refused"))
        self.assertFalse(is_rate_limit_error("", "HTTP 500: Internal Server Error"))
        self.assertFalse(is_rate_limit_error("", ""))


class BackoffTests(unittest.TestCase):
    def test_backoff_increases_with_attempts(self) -> None:
        b0 = compute_backoff_seconds(0, 30, 900, jitter_factor=0)
        b1 = compute_backoff_seconds(1, 30, 900, jitter_factor=0)
        b2 = compute_backoff_seconds(2, 30, 900, jitter_factor=0)
        self.assertEqual(b0, 30)
        self.assertEqual(b1, 60)
        self.assertEqual(b2, 120)

    def test_backoff_capped_at_maximum(self) -> None:
        b10 = compute_backoff_seconds(10, 30, 900, jitter_factor=0)
        self.assertEqual(b10, 900)

    def test_backoff_jitter_stays_in_range(self) -> None:
        for _ in range(100):
            val = compute_backoff_seconds(0, 30, 900, jitter_factor=0.25)
            self.assertGreaterEqual(val, 22.5)  # 30 - 30*0.25
            self.assertLessEqual(val, 37.5)     # 30 + 30*0.25

    def test_backoff_minimum_is_one(self) -> None:
        val = compute_backoff_seconds(0, 1, 1, jitter_factor=0.99)
        self.assertGreaterEqual(val, 1.0)


class ExecuteTaskRateLimitTests(unittest.TestCase):
    def make_settings(self) -> Settings:
        tmpdir = tempfile.mkdtemp()
        root = Path(tmpdir)
        (root / ".env").write_text("", encoding="utf-8")
        settings = Settings.load(root)
        settings.default_provider = "openrouter"
        settings.default_model = "openai/gpt-oss-20b:free"
        return settings

    def test_429_defers_task_instead_of_failing(self) -> None:
        settings = self.make_settings()

        def fake_429_runner(settings, command, **kwargs):
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="",
                stderr="API call failed after 3 retries: HTTP 429: Provider returned error",
                duration_ms=5000,
            )

        with Ledger(settings.ledger_db) as ledger:
            task = ledger.create_task(
                TaskSubmission(kind="summary", prompt="test"),
                max_attempts=5,
            )
            outcome = execute_task(settings, ledger, task["id"], command_runner=fake_429_runner)

        self.assertEqual(outcome.state, TASK_STATE_DEFERRED_RATE_LIMIT)
        self.assertIn("Rate limited", outcome.summary)

        # Verify task state in DB
        with Ledger(settings.ledger_db) as ledger:
            task = ledger.get_task(task["id"])
            self.assertEqual(task["state"], TASK_STATE_DEFERRED_RATE_LIMIT)
            self.assertEqual(task["attempt_count"], 1)
            self.assertIsNotNone(task["next_attempt_at"])

            # Verify provider cooldown was set
            cd = ledger.get_provider_cooldown("openrouter", "openai/gpt-oss-20b:free")
            self.assertIsNotNone(cd)
            run = ledger.latest_run_for_task(task["id"])

        workspace = Path(task["workspace_dir"])
        receipt = json.loads((workspace / "runs" / run["id"] / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["task_state"], TASK_STATE_DEFERRED_RATE_LIMIT)
        self.assertEqual(receipt["run_state"], TASK_STATE_FAILED)
        self.assertIn("HTTP 429", (workspace / "runs" / run["id"] / "stderr.txt").read_text(encoding="utf-8"))

    def test_429_exhausts_retries_then_fails(self) -> None:
        settings = self.make_settings()

        def fake_429_runner(settings, command, **kwargs):
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="",
                stderr="HTTP 429: rate limit",
                duration_ms=100,
            )

        with Ledger(settings.ledger_db) as ledger:
            task = ledger.create_task(
                TaskSubmission(kind="summary", prompt="test"),
                max_attempts=1,
            )
            outcome = execute_task(settings, ledger, task["id"], command_runner=fake_429_runner)

        self.assertEqual(outcome.state, TASK_STATE_FAILED)
        self.assertIn("permanently failed", outcome.summary)

    def test_non_429_failure_marks_failed_immediately(self) -> None:
        settings = self.make_settings()

        def fake_error_runner(settings, command, **kwargs):
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="",
                stderr="Connection refused",
                duration_ms=100,
            )

        with Ledger(settings.ledger_db) as ledger:
            task = ledger.create_task(TaskSubmission(kind="summary", prompt="test"))
            outcome = execute_task(settings, ledger, task["id"], command_runner=fake_error_runner)

        self.assertEqual(outcome.state, TASK_STATE_FAILED)


if __name__ == "__main__":
    unittest.main()
