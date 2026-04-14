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
    OPENROUTER_PROVIDER_ID,
    build_codex_command,
    codex_available,
    codex_bin_path,
    codex_login_authenticated,
    ensure_branch,
    make_branch_name,
    run_codex,
)
from hermes_node_ops.config import Settings


def _make_settings(*, wrapper_env: str = "", upstream_env: str = "") -> Settings:
    tmpdir = tempfile.mkdtemp()
    root = Path(tmpdir)
    upstream_home = root / "upstream-home"
    upstream_home.mkdir(parents=True, exist_ok=True)
    (upstream_home / ".env").write_text(upstream_env, encoding="utf-8")
    env_lines = [f"HERMES_NODE_UPSTREAM_HOME={upstream_home}"]
    if wrapper_env:
        env_lines.append(wrapper_env.strip())
    (root / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    return Settings.load(root)


class CodexAvailabilityTests(unittest.TestCase):
    def test_codex_not_available_when_not_installed(self) -> None:
        settings = _make_settings()
        settings.codex_bin = ""
        with patch("shutil.which", return_value=None):
            self.assertFalse(codex_available(settings))

    def test_codex_not_available_when_on_path_without_auth(self) -> None:
        settings = _make_settings()
        settings.codex_bin = ""
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            with patch("hermes_node_ops.codex.codex_login_authenticated", return_value=False):
                self.assertFalse(codex_available(settings))

    def test_codex_available_with_openai_api_key(self) -> None:
        settings = _make_settings(wrapper_env="OPENAI_API_KEY=test-openai-key")
        settings.codex_bin = ""
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            self.assertTrue(codex_available(settings))

    def test_codex_available_with_upstream_openrouter_key(self) -> None:
        settings = _make_settings(upstream_env="OPENROUTER_API_KEY=test-openrouter-key")
        settings.codex_bin = ""
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            self.assertTrue(codex_available(settings, requested_provider="openrouter"))

    def test_codex_available_via_cached_login(self) -> None:
        settings = _make_settings()
        settings.codex_bin = ""
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            with patch("hermes_node_ops.codex.codex_login_authenticated", return_value=True):
                self.assertTrue(codex_available(settings))

    def test_codex_bin_path_returns_none_when_missing(self) -> None:
        settings = _make_settings()
        settings.codex_bin = ""
        with patch("shutil.which", return_value=None):
            self.assertIsNone(codex_bin_path(settings))

    def test_codex_login_authenticated_checks_status(self) -> None:
        settings = _make_settings()
        with patch("hermes_node_ops.codex.codex_bin_path", return_value="/usr/local/bin/codex"):
            with patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["/usr/local/bin/codex", "login", "status"], 0, stdout="Logged in", stderr="",
                ),
            ) as run_mock:
                self.assertTrue(codex_login_authenticated(settings))
        self.assertEqual(run_mock.call_args.args[0], ["/usr/local/bin/codex", "login", "status"])


class CodexCommandTests(unittest.TestCase):
    def test_build_read_only_command_uses_current_cli_shape(self) -> None:
        settings = _make_settings(upstream_env="OPENROUTER_API_KEY=test-openrouter-key")
        with patch("hermes_node_ops.codex.codex_bin_path", return_value="/usr/local/bin/codex"):
            command, auth = build_codex_command(
                settings,
                prompt="inspect repo",
                repo_path="/tmp/repo",
                model="openai/gpt-oss-20b:free",
                provider="openrouter",
                writable=False,
            )
        self.assertEqual(command[:6], ["/usr/local/bin/codex", "-a", "never", "-s", "read-only", "exec"])
        self.assertIn("model_provider=\"openrouter_custom\"", command)
        self.assertIn("model_providers.openrouter_custom.name=\"OpenRouter\"", command)
        self.assertIn("--color", command)
        self.assertIn("never", command)
        self.assertEqual(command[-2:], ["/tmp/repo", "inspect repo"])
        self.assertEqual(auth.auth_mode, "openrouter_env")
        self.assertEqual(auth.provider_id, OPENROUTER_PROVIDER_ID)

    def test_build_writable_command_uses_workspace_write(self) -> None:
        settings = _make_settings(wrapper_env="OPENAI_API_KEY=test-openai-key")
        with patch("hermes_node_ops.codex.codex_bin_path", return_value="/usr/local/bin/codex"):
            command, auth = build_codex_command(
                settings,
                prompt="fix bug",
                repo_path="/tmp/repo",
                model="gpt-5.4",
                provider="openai",
                writable=True,
            )
        self.assertEqual(command[:6], ["/usr/local/bin/codex", "-a", "never", "-s", "workspace-write", "exec"])
        self.assertIn("-m", command)
        self.assertIn("gpt-5.4", command)
        self.assertEqual(auth.auth_mode, "openai_env")

    def test_build_command_raises_when_no_codex(self) -> None:
        settings = _make_settings()
        settings.codex_bin = ""
        with patch("shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                build_codex_command(
                    settings,
                    prompt="test",
                    repo_path="/tmp/repo",
                    model="gpt-5.4",
                )

    def test_build_command_raises_when_auth_missing(self) -> None:
        settings = _make_settings()
        with patch("hermes_node_ops.codex.codex_bin_path", return_value="/usr/local/bin/codex"):
            with patch("hermes_node_ops.codex.codex_login_authenticated", return_value=False):
                with self.assertRaises(RuntimeError):
                    build_codex_command(
                        settings,
                        prompt="test",
                        repo_path="/tmp/repo",
                        model="gpt-5.4",
                    )


class BranchNameTests(unittest.TestCase):
    def test_make_branch_name(self) -> None:
        name = make_branch_name("task_20260414_120000_abc123")
        self.assertEqual(name, "hermes-node/task_20260414_120000_abc123")


class RunCodexGuardrailTests(unittest.TestCase):
    def test_writable_requires_branch(self) -> None:
        settings = _make_settings()
        with self.assertRaises(ValueError):
            run_codex(
                settings,
                prompt="test",
                repo_path="/tmp/repo",
                provider="openai",
                model="gpt-5.4",
                task_id="t1",
                run_id="r1",
                writable=True,
                branch="",
            )

    def test_writable_refuses_main(self) -> None:
        settings = _make_settings()
        with self.assertRaises(ValueError):
            run_codex(
                settings,
                prompt="test",
                repo_path="/tmp/repo",
                provider="openai",
                model="gpt-5.4",
                task_id="t1",
                run_id="r1",
                writable=True,
                branch="main",
            )

    def test_writable_refuses_master(self) -> None:
        settings = _make_settings()
        with self.assertRaises(ValueError):
            run_codex(
                settings,
                prompt="test",
                repo_path="/tmp/repo",
                provider="openai",
                model="gpt-5.4",
                task_id="t1",
                run_id="r1",
                writable=True,
                branch="master",
            )

    def test_run_codex_uses_resolved_auth_and_returns_metadata(self) -> None:
        settings = _make_settings(upstream_env="OPENROUTER_API_KEY=test-openrouter-key")
        with patch("hermes_node_ops.codex.codex_bin_path", return_value="/usr/local/bin/codex"):
            with patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["/usr/local/bin/codex"], 0, stdout="codex\nCODEX_OK\n", stderr="",
                ),
            ) as run_mock:
                result = run_codex(
                    settings,
                    prompt="Reply with exactly: CODEX_OK",
                    repo_path="/tmp/repo",
                    provider="openrouter",
                    model="openai/gpt-oss-20b:free",
                    task_id="t1",
                    run_id="r1",
                )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.auth_mode, "openrouter_env")
        self.assertEqual(result.provider_id, OPENROUTER_PROVIDER_ID)
        self.assertEqual(run_mock.call_args.kwargs["env"]["OPENROUTER_API_KEY"], "test-openrouter-key")
        self.assertIn("exec", run_mock.call_args.args[0])


class EnsureBranchTests(unittest.TestCase):
    def test_creates_branch_in_real_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init", tmpdir], capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "init"],
                capture_output=True, check=True, cwd=tmpdir,
            )
            branch = ensure_branch(tmpdir, "hermes-node/test-task")
            self.assertEqual(branch, "hermes-node/test-task")
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, cwd=tmpdir, check=True,
            )
            self.assertEqual(result.stdout.strip(), "hermes-node/test-task")


if __name__ == "__main__":
    unittest.main()
