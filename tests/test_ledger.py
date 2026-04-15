from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.ledger import Ledger
from hermes_node_ops.models import (
    APPROVAL_GRANTED,
    TASK_STATE_DEFERRED_RATE_LIMIT,
    TASK_STATE_DONE,
    TASK_STATE_PENDING,
    TASK_STATE_PENDING_APPROVAL,
    TaskSubmission,
)
from hermes_node_ops.jsonl import utcnow


class LedgerTests(unittest.TestCase):
    def test_submit_task_creates_auto_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(
                    TaskSubmission(
                        kind="summary",
                        prompt="Summarize this",
                        created_by="operator",
                    )
                )
                self.assertEqual(task["state"], "pending")
                self.assertEqual(task["approval_state"], "not_required")
                self.assertTrue(task["workspace_dir"].endswith(f"/workspaces/{task['id']}"))
                approvals = ledger.approvals_for_task(task["id"])
                self.assertEqual(len(approvals), 1)
                self.assertEqual(approvals[0]["status"], "not_required")

    def test_explicit_approval_unblocks_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(
                    TaskSubmission(
                        kind="research",
                        prompt="Research this",
                        require_approval=True,
                        created_by="operator",
                    )
                )
                self.assertEqual(task["state"], "pending_approval")
                task = ledger.record_approval(
                    task["id"],
                    status=APPROVAL_GRANTED,
                    actor="operator",
                    note="Approved",
                )
                self.assertEqual(task["state"], "pending")
                self.assertEqual(task["approval_state"], "granted")

    def test_task_has_retry_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(
                    TaskSubmission(kind="summary", prompt="test"),
                    max_attempts=3,
                )
                self.assertEqual(task["attempt_count"], 0)
                self.assertEqual(task["max_attempts"], 3)
                self.assertIsNone(task["next_attempt_at"])

    def test_repo_queries_return_recent_tasks_runs_and_latest_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            repo_path = str((Path(tmpdir) / "repo").resolve())
            with Ledger(db_path) as ledger:
                task = ledger.create_task(
                    TaskSubmission(kind="repo_read", prompt="Inspect repo", repo_path=repo_path)
                )
                _, run = ledger.begin_run(
                    task["id"],
                    command=["hermes", "chat"],
                    working_dir=repo_path,
                    provider="openrouter",
                    model="openai/gpt-oss-20b:free",
                    max_turns=2,
                    executor="hermes_chat",
                )
                ledger.finish_run(
                    run["id"],
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    duration_ms=25,
                )
                ledger.add_summary(
                    task["id"],
                    run_id=run["id"],
                    content="repo looks healthy",
                )

                tasks = ledger.recent_tasks_for_repo(repo_path, limit=5)
                runs = ledger.recent_runs_for_repo(repo_path, limit=5)
                latest = ledger.latest_successful_run_for_repo(repo_path)

                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0]["id"], task["id"])
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["id"], run["id"])
                self.assertIsNotNone(latest)
                assert latest is not None
                self.assertEqual(latest["task"]["id"], task["id"])
                self.assertEqual(latest["run"]["id"], run["id"])
                self.assertEqual(latest["summary"]["content"], "repo looks healthy")


class DeferralTests(unittest.TestCase):
    def test_defer_task_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(TaskSubmission(kind="summary", prompt="test"))
                self.assertEqual(task["state"], TASK_STATE_PENDING)

                next_at = "2099-01-01T00:00:00+00:00"
                task = ledger.defer_task_rate_limit(
                    task["id"],
                    next_attempt_at=next_at,
                    reason="HTTP 429",
                )
                self.assertEqual(task["state"], TASK_STATE_DEFERRED_RATE_LIMIT)
                self.assertEqual(task["attempt_count"], 1)
                self.assertEqual(task["next_attempt_at"], next_at)

    def test_deferred_task_becomes_runnable_after_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(
                    TaskSubmission(kind="summary", prompt="test"),
                    max_attempts=5,
                )
                # Defer with a next_attempt_at in the past
                past = "2020-01-01T00:00:00+00:00"
                ledger.defer_task_rate_limit(task["id"], next_attempt_at=past)

                runnable = ledger.next_runnable_task()
                self.assertIsNotNone(runnable)
                self.assertEqual(runnable["id"], task["id"])

    def test_deferred_task_not_runnable_before_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(TaskSubmission(kind="summary", prompt="test"))
                future = "2099-01-01T00:00:00+00:00"
                ledger.defer_task_rate_limit(task["id"], next_attempt_at=future)

                runnable = ledger.next_runnable_task()
                self.assertIsNone(runnable)

    def test_deferred_task_not_runnable_when_max_attempts_reached(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(
                    TaskSubmission(kind="summary", prompt="test"),
                    max_attempts=1,
                )
                past = "2020-01-01T00:00:00+00:00"
                ledger.defer_task_rate_limit(task["id"], next_attempt_at=past)
                # attempt_count is now 1, max_attempts is 1
                runnable = ledger.next_runnable_task()
                self.assertIsNone(runnable)

    def test_deferred_tasks_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                t1 = ledger.create_task(TaskSubmission(kind="summary", prompt="a"))
                t2 = ledger.create_task(TaskSubmission(kind="summary", prompt="b"))
                ledger.defer_task_rate_limit(t1["id"], next_attempt_at="2099-01-01T00:00:00+00:00")
                deferred = ledger.deferred_tasks()
                self.assertEqual(len(deferred), 1)
                self.assertEqual(deferred[0]["id"], t1["id"])


class CooldownTests(unittest.TestCase):
    def test_set_and_get_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                self.assertIsNone(ledger.get_provider_cooldown("openrouter", "test-model"))
                ledger.set_provider_cooldown(
                    "openrouter", "test-model",
                    cooldown_until="2099-01-01T00:00:00+00:00",
                    last_error="429",
                )
                cd = ledger.get_provider_cooldown("openrouter", "test-model")
                self.assertEqual(cd, "2099-01-01T00:00:00+00:00")

    def test_cooldown_blocks_pending_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(
                    TaskSubmission(
                        kind="summary", prompt="test",
                        provider="openrouter", model="test-model",
                    )
                )
                # Set a future cooldown for that provider/model
                ledger.set_provider_cooldown(
                    "openrouter", "test-model",
                    cooldown_until="2099-01-01T00:00:00+00:00",
                )
                runnable = ledger.next_runnable_task()
                self.assertIsNone(runnable)

    def test_expired_cooldown_allows_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(
                    TaskSubmission(
                        kind="summary", prompt="test",
                        provider="openrouter", model="test-model",
                    )
                )
                ledger.set_provider_cooldown(
                    "openrouter", "test-model",
                    cooldown_until="2020-01-01T00:00:00+00:00",
                )
                runnable = ledger.next_runnable_task()
                self.assertIsNotNone(runnable)

    def test_active_cooldowns_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                ledger.set_provider_cooldown(
                    "openrouter", "model-a",
                    cooldown_until="2099-01-01T00:00:00+00:00",
                )
                ledger.set_provider_cooldown(
                    "openrouter", "model-b",
                    cooldown_until="2020-01-01T00:00:00+00:00",
                )
                active = ledger.active_cooldowns()
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0]["model"], "model-a")


class RateLimitEventTests(unittest.TestCase):
    def test_rate_limit_events_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(TaskSubmission(kind="summary", prompt="test"))
                ledger.defer_task_rate_limit(
                    task["id"],
                    next_attempt_at="2099-01-01T00:00:00+00:00",
                    reason="HTTP 429 from provider",
                )
                events = ledger.recent_rate_limit_events()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["event_type"], "task.deferred_rate_limit")
                self.assertIn("429", events[0]["message"])


class SchemaMigrationTests(unittest.TestCase):
    def test_v3_to_v4_migration_adds_workspace_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE schema_version (
                    version INTEGER NOT NULL
                );
                INSERT INTO schema_version (version) VALUES (3);

                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    state TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    max_turns INTEGER NOT NULL,
                    repo_path TEXT NOT NULL,
                    toolsets_json TEXT NOT NULL,
                    require_approval INTEGER NOT NULL DEFAULT 0,
                    approval_state TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    latest_run_id TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    next_attempt_at TEXT
                );
                """
            )
            conn.execute(
                """
                INSERT INTO tasks (
                    id, created_at, updated_at, title, kind, prompt, state, provider, model,
                    max_turns, repo_path, toolsets_json, require_approval, approval_state,
                    created_by, metadata_json, latest_run_id, attempt_count, max_attempts, next_attempt_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "task_legacy",
                    utcnow(),
                    utcnow(),
                    "legacy",
                    "summary",
                    "hello",
                    TASK_STATE_PENDING,
                    "openrouter",
                    "openai/gpt-oss-20b:free",
                    2,
                    "",
                    "[]",
                    0,
                    "not_required",
                    "operator",
                    "{}",
                    "",
                    0,
                    5,
                    None,
                ),
            )
            conn.commit()
            conn.close()

            with Ledger(db_path) as ledger:
                task = ledger.get_task("task_legacy")
                self.assertTrue(task["workspace_dir"].endswith("/workspaces/task_legacy"))
                self.assertEqual(ledger._get_schema_version(), 4)

    def test_migration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(TaskSubmission(kind="summary", prompt="test"))
            # Re-open — migration runs again, should not fail
            with Ledger(db_path) as ledger:
                task2 = ledger.get_task(task["id"])
                self.assertEqual(task2["state"], task["state"])
                self.assertEqual(task2["workspace_dir"], task["workspace_dir"])


if __name__ == "__main__":
    unittest.main()
