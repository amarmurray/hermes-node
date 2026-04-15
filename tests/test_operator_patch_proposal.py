"""Tests for patch proposal artifacts."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.harness.store import ensure_harness_layout
from hermes_node_ops.operator.patch_proposal import (
    PROPOSAL_STATES,
    create_proposal,
    list_proposals,
    load_proposal,
    update_proposal_state,
)


class PatchProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo_root = self.tmp / "hermes"
        self.repo_root.mkdir()
        self.session_id = "sess-test"
        ensure_harness_layout(self.repo_root, self.session_id)

    def test_create_and_load(self) -> None:
        p = create_proposal(
            self.repo_root,
            self.session_id,
            repo_name="live",
            repo_path="/tmp/live",
            goal="Fix the thing",
            branch_prefix="live/auto/",
            files_in_scope=["a.py"],
            risk_notes=["touches hot path"],
            provenance=["run-123"],
        )
        self.assertEqual(p.state, "draft")
        self.assertTrue(p.branch.startswith("live/auto/"))
        loaded = load_proposal(self.repo_root, self.session_id, p.patch_id)
        self.assertEqual(loaded.goal, "Fix the thing")
        self.assertEqual(loaded.files_in_scope, ("a.py",))

    def test_branch_auto_slug(self) -> None:
        p = create_proposal(
            self.repo_root,
            self.session_id,
            repo_name="live",
            repo_path="/tmp/live",
            goal="Patch: Fix the $crazy bug!",
            branch_prefix="x/auto/",
        )
        self.assertTrue(p.branch.startswith("x/auto/"))
        self.assertNotIn("$", p.branch)

    def test_explicit_branch_override(self) -> None:
        p = create_proposal(
            self.repo_root,
            self.session_id,
            repo_name="live",
            repo_path="/tmp/live",
            goal="g",
            branch_prefix="x/auto/",
            branch="custom/branch",
        )
        self.assertEqual(p.branch, "custom/branch")

    def test_update_state(self) -> None:
        p = create_proposal(
            self.repo_root,
            self.session_id,
            repo_name="live",
            repo_path="/tmp/live",
            goal="g",
            branch_prefix="x/",
        )
        updated = update_proposal_state(
            self.repo_root,
            self.session_id,
            p.patch_id,
            state="submitted",
            task_id="task-1",
        )
        self.assertEqual(updated.state, "submitted")
        self.assertEqual(updated.task_id, "task-1")

    def test_unknown_state_rejected(self) -> None:
        p = create_proposal(
            self.repo_root,
            self.session_id,
            repo_name="live",
            repo_path="/tmp/live",
            goal="g",
            branch_prefix="x/",
        )
        with self.assertRaises(ValueError):
            update_proposal_state(
                self.repo_root, self.session_id, p.patch_id, state="bogus"
            )

    def test_list_proposals_sorted(self) -> None:
        for i in range(3):
            create_proposal(
                self.repo_root,
                self.session_id,
                repo_name="live",
                repo_path="/tmp/live",
                goal=f"g{i}",
                branch_prefix="x/",
            )
        props = list_proposals(self.repo_root, self.session_id)
        self.assertEqual(len(props), 3)

    def test_states_enum(self) -> None:
        self.assertIn("draft", PROPOSAL_STATES)
        self.assertIn("submitted", PROPOSAL_STATES)
        self.assertIn("succeeded", PROPOSAL_STATES)


if __name__ == "__main__":
    unittest.main()
