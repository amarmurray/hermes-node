"""Tests for the specialist seam extension."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.temporal_seam import (  # noqa: E402
    SpecialistInvocation,
    SpecialistOutcome,
    invoke_specialist_once,
)


def _fake_settings(repo_root: Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        repo_root=repo_root,
        ledger_db=repo_root / "state" / "ledger.sqlite",
    )


class TemporalSeamSpecialistTests(unittest.TestCase):
    def test_patch_review_not_found_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _fake_settings(Path(tmp))
            outcome = invoke_specialist_once(
                settings,
                SpecialistInvocation(
                    role="patch-review",
                    goal="check",
                    context={"proposal_id": "missing"},
                ),
            )
            self.assertIsInstance(outcome, SpecialistOutcome)
            self.assertTrue(outcome.report)
            self.assertEqual(outcome.error, "")
            self.assertIn("not found", outcome.report.lower())

    def test_unknown_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _fake_settings(Path(tmp))
            outcome = invoke_specialist_once(
                settings,
                SpecialistInvocation(role="nope", goal="check"),
            )
            self.assertEqual(outcome.report, "")
            self.assertIn("unknown role", outcome.error)


if __name__ == "__main__":
    unittest.main()
