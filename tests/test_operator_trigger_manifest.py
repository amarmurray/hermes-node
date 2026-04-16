"""Tests for the trigger manifest scaffolding."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.trigger_manifest import (  # noqa: E402
    TriggerManifest,
    build_trigger_manifest,
)


class TriggerManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = build_trigger_manifest()

    def test_has_subscriptions(self) -> None:
        self.assertGreater(len(self.manifest.subscriptions), 0)

    def test_all_disabled(self) -> None:
        for sub in self.manifest.subscriptions:
            self.assertFalse(sub.enabled, f"{sub.specialist_role}/{sub.event_category} should be disabled")

    def test_for_specialist_rate_limit_triage(self) -> None:
        subs = self.manifest.for_specialist("rate-limit-triage")
        self.assertGreater(len(subs), 0)
        events = {s.event_category for s in subs}
        self.assertIn("rate_limit_hit", events)
        self.assertIn("schedule_tick", events)

    def test_for_event_schedule_tick_multiple_specialists(self) -> None:
        subs = self.manifest.for_event("schedule_tick")
        roles = {s.specialist_role for s in subs}
        self.assertGreater(len(roles), 1, "schedule_tick should come from multiple specialists")

    def test_unknown_specialist_returns_empty(self) -> None:
        self.assertEqual(self.manifest.for_specialist("nonexistent"), ())

    def test_unknown_event_returns_empty(self) -> None:
        self.assertEqual(self.manifest.for_event("nonexistent_event"), ())

    def test_to_dict_shape(self) -> None:
        d = self.manifest.to_dict()
        self.assertIn("subscriptions", d)
        self.assertIn("total", d)
        self.assertIn("enabled_count", d)
        self.assertEqual(d["enabled_count"], 0)
        self.assertEqual(d["total"], len(self.manifest.subscriptions))

    def test_empty_manifest(self) -> None:
        empty = TriggerManifest()
        self.assertEqual(len(empty.subscriptions), 0)
        self.assertEqual(empty.for_specialist("any"), ())
        self.assertEqual(empty.for_event("any"), ())


if __name__ == "__main__":
    unittest.main()
