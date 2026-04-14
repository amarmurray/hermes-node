from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.codex import (
    build_codex_command,
    codex_available,
    codex_bin_path,
    ensure_branch,
    make_branch_name,
    run_codex,
)
from hermes_node_ops.config import Settings


def _make_settings() -> Settings:
    tmpdir = tempfile.mkdtemp()
    root = Path(tmpdir)
    (root / ".env").write_text("", encoding="utf-8")
    return Settings.load(root)


class CodexAvailabilityTests(unittest.TestCase):
    def test_codex_not_available_when_not_installed(self) -> None:
        settings = _make_settings()
        settings.codex_bin = ""
        with patch("shutil.which", return_value=None):
            self.assertFalse(codex_available(settings))

    def test_codex_available_when_on_path(self) -> None:
        settings = _make_settings()
        settings.codex_bin = ""
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            self.assertTrue(codex_available(settings))

    def test_codex_available_via_explicit_setting(self) -> None:
        settings = _make_settings()
        with tempfile.NamedTemporaryFile(suffix="codex") as f:
            settings.codex_bin = f.name
            self.assertTrue(codex_available(settings))

    def test_codex_bin_path_returns_none_when_missing(self) -> None:
        settings = _make_settings()
        settings.codex_bin = ""
        with patch("shutil.which", return_value=None):
            self.assertIsNone(codex_bin_path(settings))


class CodexCommandTests(unittest.TestCase):
    def test_build_read_only_command(self) -> None:
        settings = _make_settings()
        with patch("hermes_node_ops.codex.codex_bin_path", return_value="/usr/local/bin/codex"):
            cmd = build_codex_command(
                settings, prompt="explain this", repo_path="/tmp/repo", writable=False,
            )
        self.assertIn("--approval-mode", cmd)
        self.assertIn("suggest", cmd)
        self.assertIn("explain this", cmd)

    def test_build_writable_command(self) -> None:
        settings = _make_settings()
        with patch("hermes_node_ops.codex.codex_bin_path", return_value="/usr/local/bin/codex"):
            cmd = build_codex_command(
                settings, prompt="fix bug", repo_path="/tmp/repo", writable=True,
            )
        self.assertIn("auto-edit", cmd)

    def test_build_command_raises_when_no_codex(self) -> None:
        settings = _make_settings()
        settings.codex_bin = ""
        with patch("shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                build_codex_command(settings, prompt="test", repo_path="/tmp/repo")


class BranchNameTests(unittest.TestCase):
    def test_make_branch_name(self) -> None:
        name = make_branch_name("task_20260414_120000_abc123")
        self.assertEqual(name, "hermes-node/task_20260414_120000_abc123")


class RunCodexGuardrailTests(unittest.TestCase):
    def test_writable_requires_branch(self) -> None:
        settings = _make_settings()
        with self.assertRaises(ValueError):
            run_codex(
                settings, prompt="test", repo_path="/tmp/repo",
                task_id="t1", run_id="r1", writable=True, branch="",
            )

    def test_writable_refuses_main(self) -> None:
        settings = _make_settings()
        with self.assertRaises(ValueError):
            run_codex(
                settings, prompt="test", repo_path="/tmp/repo",
                task_id="t1", run_id="r1", writable=True, branch="main",
            )

    def test_writable_refuses_master(self) -> None:
        settings = _make_settings()
        with self.assertRaises(ValueError):
            run_codex(
                settings, prompt="test", repo_path="/tmp/repo",
                task_id="t1", run_id="r1", writable=True, branch="master",
            )


class EnsureBranchTests(unittest.TestCase):
    def test_creates_branch_in_real_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Init a real git repo
            subprocess.run(["git", "init", tmpdir], capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "init"],
                capture_output=True, check=True, cwd=tmpdir,
            )
            branch = ensure_branch(tmpdir, "hermes-node/test-task")
            self.assertEqual(branch, "hermes-node/test-task")
            # Verify we're on that branch
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, cwd=tmpdir, check=True,
            )
            self.assertEqual(result.stdout.strip(), "hermes-node/test-task")


if __name__ == "__main__":
    unittest.main()
