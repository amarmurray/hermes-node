"""Tests for session project stamping and filtering."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.session import (  # noqa: E402
    create_session,
    list_sessions,
    read_session_project,
)


class SessionProjectTests(unittest.TestCase):
    def test_create_session_with_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            sid = create_session(
                repo_root,
                model="m",
                project={
                    "repo_root": "/r",
                    "branch": "main",
                    "worktree": "",
                    "repo_name": "ixion",
                    "cwd": "/r",
                },
            )
            project = read_session_project(repo_root, sid)
            self.assertEqual(project.get("repo_name"), "ixion")
            self.assertEqual(project.get("branch"), "main")

    def test_list_sessions_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            sid_a = create_session(
                repo_root,
                project={
                    "repo_root": "/a",
                    "branch": "main",
                    "worktree": "",
                    "repo_name": "ixion",
                    "cwd": "/a",
                },
            )
            sid_b = create_session(
                repo_root,
                project={
                    "repo_root": "/b",
                    "branch": "main",
                    "worktree": "",
                    "repo_name": "other",
                    "cwd": "/b",
                },
            )
            sid_c = create_session(repo_root)  # no project
            filtered = list_sessions(repo_root, project_repo_name="ixion")
            ids = [s["session_id"] for s in filtered]
            self.assertIn(sid_a, ids)
            self.assertNotIn(sid_b, ids)
            self.assertNotIn(sid_c, ids)

            all_sessions = list_sessions(repo_root)
            all_ids = [s["session_id"] for s in all_sessions]
            self.assertIn(sid_a, all_ids)
            self.assertIn(sid_b, all_ids)
            self.assertIn(sid_c, all_ids)

    def test_read_session_project_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            sid = create_session(repo_root)
            self.assertEqual(read_session_project(repo_root, sid), {})


if __name__ == "__main__":
    unittest.main()
