from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.config import Settings
from hermes_node_ops.jsonl import append_jsonl
from hermes_node_ops.wrapper import (
    build_api_submit_request,
    build_service_command,
    build_submit_command,
)


class WrapperCommandTests(unittest.TestCase):
    def make_settings(self) -> Settings:
        tmpdir = tempfile.mkdtemp()
        root = Path(tmpdir)
        (root / ".env").write_text("", encoding="utf-8")
        settings = Settings.load(root)
        settings.systemd_scope = "user"
        settings.service_user = "ubuntu"
        settings.api_key = "secret"
        settings.api_base_url = "http://127.0.0.1:8642/v1"
        return settings

    def test_build_user_service_command(self) -> None:
        settings = self.make_settings()
        command = build_service_command(settings, "install", system=False)
        self.assertEqual(command[-2:], ["gateway", "install"])

    def test_build_system_service_command(self) -> None:
        settings = self.make_settings()
        command = build_service_command(settings, "install", system=True)
        self.assertEqual(command[:1], ["sudo"])
        self.assertIn("--system", command)
        self.assertEqual(command[-2:], ["--run-as-user", "ubuntu"])

    def test_build_submit_command_includes_provider_and_model(self) -> None:
        settings = self.make_settings()
        command = build_submit_command(
            settings,
            query="hello",
            provider="openrouter",
            model="anthropic/claude-sonnet-4.6",
            quiet=True,
            max_turns=3,
            worktree=True,
            yolo=True,
        )
        self.assertEqual(command[1:4], ["chat", "-q", "hello"])
        self.assertIn("-Q", command)
        self.assertIn("--provider", command)
        self.assertIn("openrouter", command)
        self.assertIn("--model", command)
        self.assertIn("anthropic/claude-sonnet-4.6", command)
        self.assertIn("--worktree", command)
        self.assertIn("--yolo", command)

    def test_build_api_submit_request(self) -> None:
        settings = self.make_settings()
        url, headers, body = build_api_submit_request(
            settings,
            input_text="hello",
            instructions="be brief",
            conversation="ops",
            store=True,
        )
        self.assertEqual(url, "http://127.0.0.1:8642/v1/responses")
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(body["conversation"], "ops")

    def test_append_jsonl_writes_single_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "log.jsonl"
            append_jsonl(path, {"action": "test", "ok": True})
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["action"], "test")


if __name__ == "__main__":
    unittest.main()
