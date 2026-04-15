"""Tests for the operator repo registry."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.repo_registry import (
    BranchPolicy,
    RepoEntry,
    RepoRegistry,
    check_presence,
    list_presence,
    render_registry,
)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=path,
        check=True,
    )


def _write_registry(repo_root: Path, payload: dict) -> None:
    cfg_dir = repo_root / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "repos.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


class RepoRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo_root = self.tmp / "hermes"
        self.repo_root.mkdir()
        self.live = self.tmp / "live-repo"
        self.live.mkdir()
        _git_init(self.live)

    def test_load_empty_when_missing(self) -> None:
        reg = RepoRegistry.load(self.repo_root)
        self.assertEqual(reg.repos, ())
        self.assertEqual(reg.future_slots, ())

    def test_load_finds_entries_and_aliases(self) -> None:
        _write_registry(
            self.repo_root,
            {
                "version": 1,
                "repos": [
                    {
                        "name": "live",
                        "path": str(self.live),
                        "aliases": ["l", "livealias"],
                        "allowed_kinds": ["repo_patch_branch"],
                        "branch_policy": {
                            "default_branch": "main",
                            "branch_prefix": "live/auto/",
                            "require_clean_worktree": True,
                        },
                    }
                ],
                "future_slots": [{"name": "future"}],
            },
        )
        reg = RepoRegistry.load(self.repo_root)
        self.assertEqual(len(reg.repos), 1)
        entry = reg.find("livealias")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.name, "live")
        self.assertTrue(entry.allows_kind("repo_patch_branch"))
        self.assertEqual(entry.branch_policy.branch_prefix, "live/auto/")
        self.assertEqual(len(reg.future_slots), 1)

    def test_require_unknown_raises(self) -> None:
        reg = RepoRegistry.load(self.repo_root)
        with self.assertRaises(KeyError):
            reg.require("nope")

    def test_duplicate_alias_rejected(self) -> None:
        _write_registry(
            self.repo_root,
            {
                "version": 1,
                "repos": [
                    {"name": "a", "path": str(self.live), "aliases": ["x"]},
                    {"name": "b", "path": str(self.live), "aliases": ["x"]},
                ],
            },
        )
        with self.assertRaises(ValueError):
            RepoRegistry.load(self.repo_root)

    def test_check_presence_live_repo(self) -> None:
        entry = RepoEntry(name="live", path=str(self.live))
        presence = check_presence(entry)
        self.assertTrue(presence.exists)
        self.assertTrue(presence.is_git)
        self.assertTrue(presence.ready_for_mutation)
        self.assertEqual(presence.branch, "main")

    def test_check_presence_absent(self) -> None:
        entry = RepoEntry(name="gone", path=str(self.tmp / "nope"))
        presence = check_presence(entry)
        self.assertFalse(presence.exists)
        self.assertFalse(presence.ready_for_mutation)
        self.assertIn("does not exist", presence.missing_reason)

    def test_check_presence_not_git(self) -> None:
        bare = self.tmp / "bare"
        bare.mkdir()
        entry = RepoEntry(name="bare", path=str(bare))
        presence = check_presence(entry)
        self.assertTrue(presence.exists)
        self.assertFalse(presence.is_git)
        self.assertFalse(presence.ready_for_mutation)

    def test_render_registry_shape(self) -> None:
        _write_registry(
            self.repo_root,
            {
                "version": 1,
                "repos": [
                    {"name": "live", "path": str(self.live)},
                    {"name": "missing", "path": str(self.tmp / "nope")},
                ],
                "future_slots": [{"name": "treasuryos"}],
            },
        )
        reg = RepoRegistry.load(self.repo_root)
        rendered = render_registry(reg)
        self.assertEqual(rendered["counts"]["total"], 2)
        self.assertEqual(rendered["counts"]["present"], 1)
        self.assertEqual(rendered["counts"]["absent"], 1)
        self.assertEqual(rendered["counts"]["future"], 1)


if __name__ == "__main__":
    unittest.main()
