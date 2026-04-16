"""Tests for Phase 4 specialist additions: patch-review + allowed surfaces."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.harness.specialists import (  # noqa: E402
    SPECIALIST_ROLES,
    enforce_allowed_surfaces,
    get_specialist_spec,
    run_specialist,
)


class Phase4SpecialistTests(unittest.TestCase):
    def test_patch_review_registered(self) -> None:
        self.assertIn("patch-review", SPECIALIST_ROLES)

    def test_patch_review_spec_surfaces(self) -> None:
        spec = get_specialist_spec("patch-review")
        self.assertTrue(spec.allowed_surfaces)
        self.assertIn("read_patch_proposal", spec.allowed_surfaces)

    def test_enforce_blocks_forbidden(self) -> None:
        self.assertFalse(enforce_allowed_surfaces("patch-review", "submit_task"))

    def test_enforce_allows_whitelisted(self) -> None:
        self.assertTrue(
            enforce_allowed_surfaces("patch-review", "read_patch_proposal")
        )

    def test_enforce_unknown_role_is_false(self) -> None:
        self.assertFalse(enforce_allowed_surfaces("unknown-role", "anything"))

    def test_patch_review_not_found_report(self) -> None:
        report = run_specialist(
            "patch-review",
            "review proposal",
            {"proposal_id": "does-not-exist"},
            repo_path="",
        )
        self.assertIsInstance(report, str)
        self.assertIn("not found", report.lower())


if __name__ == "__main__":
    unittest.main()
