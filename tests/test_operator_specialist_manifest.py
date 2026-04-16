"""Tests for the specialist manifest renderer."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.harness.specialists import render_specialist_manifest  # noqa: E402


class SpecialistManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = render_specialist_manifest()

    def test_has_specialists_list(self) -> None:
        self.assertIn("specialists", self.manifest)
        self.assertIsInstance(self.manifest["specialists"], list)

    def test_four_specialists(self) -> None:
        self.assertEqual(len(self.manifest["specialists"]), 4)

    def test_total_count(self) -> None:
        self.assertEqual(self.manifest["total"], 4)

    def test_each_specialist_has_required_fields(self) -> None:
        required = {
            "role", "purpose", "read_only", "risk_class",
            "autonomy_eligible", "trigger_events", "callable_only",
        }
        for spec in self.manifest["specialists"]:
            for field in required:
                self.assertIn(field, spec, f"missing {field} in {spec.get('role', '?')}")

    def test_autonomy_eligible_count_is_three(self) -> None:
        self.assertEqual(self.manifest["autonomy_eligible_count"], 3)

    def test_all_callable_only_true(self) -> None:
        for spec in self.manifest["specialists"]:
            self.assertTrue(spec["callable_only"], f"{spec['role']} should be callable_only")

    def test_all_read_only_true(self) -> None:
        for spec in self.manifest["specialists"]:
            self.assertTrue(spec["read_only"], f"{spec['role']} should be read_only")

    def test_all_risk_class_read_only(self) -> None:
        for spec in self.manifest["specialists"]:
            self.assertEqual(spec["risk_class"], "read_only")

    def test_each_specialist_has_purpose(self) -> None:
        for spec in self.manifest["specialists"]:
            self.assertTrue(spec["purpose"], f"{spec['role']} should have a purpose")

    def test_trigger_events_are_lists(self) -> None:
        for spec in self.manifest["specialists"]:
            self.assertIsInstance(spec["trigger_events"], list)


if __name__ == "__main__":
    unittest.main()
