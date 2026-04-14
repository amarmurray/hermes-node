from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.workspaces import (
    cleanup_workspace,
    ensure_workspace,
    read_workspace_manifest,
    write_input_artifact,
    write_run_output,
    write_run_receipt,
    write_worktree_metadata,
)


class WorkspaceArtifactTests(unittest.TestCase):
    def test_write_input_artifact_only_writes_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = write_input_artifact(
                root,
                "task_1",
                kind="summary",
                prompt="first prompt",
                provider="openrouter",
                model="openai/gpt-oss-20b:free",
                created_at="2026-04-14T00:00:00+00:00",
            )
            second = write_input_artifact(
                root,
                "task_1",
                kind="summary",
                prompt="second prompt",
                provider="openrouter",
                model="openai/gpt-oss-20b:free",
                created_at="2026-04-14T01:00:00+00:00",
            )

            self.assertEqual(first, second)
            content = first.read_text(encoding="utf-8")
            self.assertIn("first prompt", content)
            self.assertNotIn("second prompt", content)

    def test_run_receipt_and_manifest_capture_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ensure_workspace(root, "task_2")
            write_input_artifact(root, "task_2", kind="repo_read", prompt="inspect repo", repo_path="/tmp/repo")
            write_run_output(root, "task_2", "run_1", stdout="hello\n", stderr="warn\n")
            write_run_receipt(
                root,
                "task_2",
                "run_1",
                exit_code=0,
                duration_ms=125,
                executor="hermes_chat",
                provider="openrouter",
                model="openai/gpt-oss-20b:free",
                state="done",
                created_at="2026-04-14T00:00:00+00:00",
                started_at="2026-04-14T00:00:01+00:00",
                finished_at="2026-04-14T00:00:02+00:00",
                working_dir="/tmp/repo",
                command=["hermes", "chat", "-q", "inspect repo"],
                extra={"task_state": "done", "run_state": "done"},
            )
            write_worktree_metadata(
                root,
                "task_2",
                repo_path="/tmp/repo",
                branch="feature/test",
                worktree_path="/tmp/repo",
                extra={"uses_upstream_worktree": True},
            )

            manifest = read_workspace_manifest(root, "task_2")

            self.assertTrue(manifest["has_input"])
            self.assertTrue(manifest["has_worktree_meta"])
            self.assertEqual(len(manifest["runs"]), 1)
            receipt = manifest["runs"][0]["receipt"]
            self.assertEqual(receipt["executor"], "hermes_chat")
            self.assertEqual(receipt["provider"], "openrouter")
            self.assertEqual(receipt["model"], "openai/gpt-oss-20b:free")
            self.assertEqual(receipt["started_at"], "2026-04-14T00:00:01+00:00")
            self.assertEqual(receipt["finished_at"], "2026-04-14T00:00:02+00:00")
            self.assertEqual(receipt["task_state"], "done")
            self.assertEqual(receipt["run_state"], "done")
            self.assertEqual(manifest["worktree"]["branch"], "feature/test")
            self.assertTrue(manifest["worktree"]["uses_upstream_worktree"])

    def test_cleanup_workspace_removes_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ensure_workspace(root, "task_3")
            notes_dir = root / "state" / "workspaces" / "task_3" / "notes"
            notes_dir.mkdir(parents=True, exist_ok=True)
            (notes_dir / "note.txt").write_text("operator note\n", encoding="utf-8")

            self.assertTrue(cleanup_workspace(root, "task_3"))
            self.assertFalse((root / "state" / "workspaces" / "task_3").exists())
            self.assertFalse(cleanup_workspace(root, "task_3"))

    def test_running_receipt_keeps_finished_at_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            write_run_receipt(
                root,
                "task_4",
                "run_1",
                exit_code=None,
                duration_ms=None,
                executor="hermes_chat",
                provider="openrouter",
                model="openai/gpt-oss-20b:free",
                state="running",
                started_at="2026-04-14T00:00:01+00:00",
            )
            receipt = json.loads(
                (root / "state" / "workspaces" / "task_4" / "runs" / "run_1" / "receipt.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(receipt["finished_at"], "")


if __name__ == "__main__":
    unittest.main()
