from __future__ import annotations

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
    def test_migration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.db"
            with Ledger(db_path) as ledger:
                task = ledger.create_task(TaskSubmission(kind="summary", prompt="test"))
            # Re-open — migration runs again, should not fail
            with Ledger(db_path) as ledger:
                task2 = ledger.get_task(task["id"])
                self.assertEqual(task2["state"], task["state"])


if __name__ == "__main__":
    unittest.main()
