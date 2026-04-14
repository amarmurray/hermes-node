from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.ledger import Ledger
from hermes_node_ops.models import APPROVAL_GRANTED, TaskSubmission


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
                self.assertEqual(task["state"], "waiting_approval")
                task = ledger.record_approval(
                    task["id"],
                    status=APPROVAL_GRANTED,
                    actor="operator",
                    note="Approved",
                )
                self.assertEqual(task["state"], "pending")
                self.assertEqual(task["approval_state"], "granted")


if __name__ == "__main__":
    unittest.main()
