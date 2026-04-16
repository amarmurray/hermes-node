"""Tests for operator.constitution.build_system_prompt layering."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.constitution import (  # noqa: E402
    OPERATOR_SYSTEM_PROMPT,
    build_system_prompt,
)


class ConstitutionTests(unittest.TestCase):
    def test_project_memory_layered_in(self) -> None:
        out = build_system_prompt(project_memory="ZZZPROJECTMEMORYZZZ")
        self.assertIn("ZZZPROJECTMEMORYZZZ", out)
        # Base constitution still present.
        base_fragment = OPERATOR_SYSTEM_PROMPT.splitlines()[0].strip()
        self.assertIn(base_fragment, out)


if __name__ == "__main__":
    unittest.main()
