"""Tests for Phase 3 capability and workflow registries."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.hermes_capabilities import render_hermes_capability_registry
from hermes_node_ops.operator.workflow_inventory import render_workflow_inventory


def test_capability_registry_groups_statuses() -> None:
    rendered = render_hermes_capability_registry()
    assert "wrapped" in rendered
    assert "candidate" in rendered
    assert "withheld" in rendered
    assert any(item["name"] == "upstream_cli_runtime" for item in rendered["wrapped"])


def test_workflow_inventory_includes_temporal_candidates() -> None:
    rendered = render_workflow_inventory()
    names = {candidate["name"] for candidate in rendered["candidates"]}
    assert "repeated_repo_diagnostics" in names
    assert "approval_wait_retry_flows" in names
