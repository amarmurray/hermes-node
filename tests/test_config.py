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
                        "HERMES_NODE_UPSTREAM_REPO=./apps/hermes-agent",
                        "HERMES_NODE_HERMES_BIN=./bin/hermes",
                        "HERMES_NODE_DB_PATH=./state/ledger.db",
                        "HERMES_NODE_LOG_JSONL=./logs/wrapper.jsonl",
                        "HERMES_NODE_SERVICE_USER=ubuntu",
                        "HERMES_NODE_DEFAULT_PROVIDER=openrouter",
                        "HERMES_NODE_DEFAULT_MODEL=openai/gpt-oss-20b:free",
                        "HERMES_NODE_DEFAULT_MAX_TURNS=6",
                        "HERMES_NODE_POLL_INTERVAL_SECONDS=22",
                        "HERMES_NODE_SOURCE_TAG=tool",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            settings = Settings.load(root)
            self.assertEqual(settings.upstream_home, (root / "state" / "hermes-home").resolve())
            self.assertEqual(settings.upstream_repo, (root / "apps" / "hermes-agent").resolve())
            self.assertEqual(settings.hermes_bin, (root / "bin" / "hermes").resolve())
            self.assertEqual(settings.ledger_db, (root / "state" / "ledger.db").resolve())
            self.assertEqual(settings.log_jsonl, (root / "logs" / "wrapper.jsonl").resolve())
            self.assertEqual(settings.default_provider, "openrouter")
            self.assertEqual(settings.default_model, "openai/gpt-oss-20b:free")
            self.assertEqual(settings.default_max_turns, 6)
            self.assertEqual(settings.poll_interval_seconds, 22)
            self.assertEqual(settings.max_attempts, 5)
            self.assertEqual(settings.base_backoff_seconds, 30)
            self.assertEqual(settings.max_backoff_seconds, 900)


if __name__ == "__main__":
    unittest.main()
