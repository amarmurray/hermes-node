"""Tests for Phase 4 specialist additions: patch-review + allowed surfaces."""

from __future__ import annotations

import os
import sys
import tempfile
import time
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

    def test_repo_diagnostician_autonomy_eligible(self) -> None:
        spec = get_specialist_spec("repo-diagnostician")
        self.assertTrue(spec.autonomy_eligible)

    def test_patch_review_not_autonomy_eligible(self) -> None:
        spec = get_specialist_spec("patch-review")
        self.assertFalse(spec.autonomy_eligible)

    def test_patch_review_not_found_report(self) -> None:
        report = run_specialist(
            "patch-review",
            "review proposal",
            {"proposal_id": "does-not-exist"},
            repo_path="",
        )
        self.assertIsInstance(report, str)
        self.assertIn("not found", report.lower())

    def test_patch_review_not_found_has_bottom_line(self) -> None:
        report = run_specialist(
            "patch-review",
            "review",
            {"proposal_id": "does-not-exist"},
        )
        self.assertIn("## Bottom line", report)

    def test_patch_review_auto_resolves_latest_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "state" / "operator_sessions"
            # two proposals, different sessions and mtimes
            older = sessions / "sess_a" / "patches" / "patch_old" / "proposal.md"
            older.parent.mkdir(parents=True, exist_ok=True)
            older.write_text(
                "# Patch Proposal (old)\n\n- repo: /tmp/foo\n", encoding="utf-8"
            )
            newer = sessions / "sess_b" / "patches" / "patch_new" / "proposal.md"
            newer.parent.mkdir(parents=True, exist_ok=True)
            newer.write_text(
                "# Patch Proposal (new)\n\n- repo: /tmp/bar\n", encoding="utf-8"
            )
            # Force newer mtime to be later than older.
            now = time.time()
            os.utime(older, (now - 100, now - 100))
            os.utime(newer, (now, now))

            report = run_specialist(
                "patch-review",
                "review the latest",
                {"repo_root": str(root)},
            )
            self.assertIn("## Bottom line", report)
            self.assertIn("patch_new", report)
            self.assertNotIn("not found", report.lower())

    def test_patch_review_has_bottom_line_when_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prop = root / "proposal.md"
            prop.write_text(
                "# Proposal\n\n- repo: /tmp/myrepo\n", encoding="utf-8"
            )
            report = run_specialist(
                "patch-review",
                "review",
                {"proposal_path": str(prop)},
            )
            self.assertIn("## Bottom line", report)
            self.assertIn("/tmp/myrepo", report)


if __name__ == "__main__":
    unittest.main()
