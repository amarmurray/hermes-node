from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.config import Settings


class SettingsTests(unittest.TestCase):
    def test_load_defaults_from_repo_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "HERMES_NODE_UPSTREAM_HOME=./state/hermes-home",
                        "HERMES_NODE_HERMES_BIN=./bin/hermes",
                        "HERMES_NODE_LOG_JSONL=./logs/wrapper.jsonl",
                        "HERMES_NODE_SYSTEMD_SCOPE=system",
                        "HERMES_NODE_SERVICE_USER=ubuntu",
                        "HERMES_NODE_ENABLE_API_SERVER=true",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            settings = Settings.load(root)
            self.assertEqual(settings.upstream_home, (root / "state" / "hermes-home").resolve())
            self.assertEqual(settings.hermes_bin, (root / "bin" / "hermes").resolve())
            self.assertEqual(settings.log_jsonl, (root / "logs" / "wrapper.jsonl").resolve())
            self.assertEqual(settings.systemd_scope, "system")
            self.assertEqual(settings.service_user, "ubuntu")
            self.assertTrue(settings.enable_api_server)


if __name__ == "__main__":
    unittest.main()
