from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("offline_demo", ROOT / "scripts" / "offline_demo.py")
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


class OfflineDemoTests(unittest.TestCase):
    def test_real_lifecycle_and_receipts_without_external_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "demo"
            # Fail if the demo accidentally crosses a live boundary or loads credentials.
            with (
                patch("subprocess.Popen", side_effect=AssertionError("No subprocess in offline demo")),
                patch("socket.socket.connect", side_effect=AssertionError("No network in offline demo")),
                patch("hermes_node_ops.config.Settings.load", side_effect=AssertionError("No .env loading")),
                patch("hermes_node_ops.wrapper.codex_available", side_effect=AssertionError("No auth probing")),
            ):
                result = demo.run_demo(output, emit=lambda _: None)

            self.assertEqual(result["state_transitions"], ["pending_approval", "pending", "deferred_rate_limit", "done"])
            self.assertEqual(result["run_exit_codes"], [1, 0])
            self.assertEqual(result["mock_provider_calls"], 2)
            self.assertTrue(result["persisted_after_reopen"])
            self.assertEqual(result["real_repository_tasks_completed"], 0)
            self.assertEqual(result["live_model_calls"], 0)
            self.assertEqual([row["status"] for row in result["approvals"]], ["requested", "granted"])
            for relative_path in result["receipts"]:
                receipt = json.loads((output / relative_path).read_text(encoding="utf-8"))
                self.assertEqual(receipt["provider"], "offline-demo")
                self.assertEqual(receipt["model"], "deterministic-fixture-v1")
            self.assertIn("scripted", (output / "demo-summary.json").read_text(encoding="utf-8"))

    def test_refuses_to_overwrite_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            existing = output / "keep.txt"
            existing.write_text("keep me", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "new or empty"):
                demo.run_demo(output, emit=lambda _: None)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(list(output.iterdir()), [existing])


if __name__ == "__main__":
    unittest.main()
