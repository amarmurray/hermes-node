"""Inventory of repeated workflows that fit future Temporal orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowCandidate:
    name: str
    stage: str
    summary: str
    temporal_fit: str
    guardrails: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stage": self.stage,
            "summary": self.summary,
            "temporal_fit": self.temporal_fit,
            "guardrails": list(self.guardrails),
        }


WORKFLOW_CANDIDATES: tuple[WorkflowCandidate, ...] = (
    WorkflowCandidate(
        name="repeated_repo_diagnostics",
        stage="later",
        summary="Run recurring repo state, changed-file, and relevance diagnostics across registered repos.",
        temporal_fit="High; periodic durable loop with stable read-only steps.",
        guardrails=("registered repos only", "read-only tools", "receipt-emitting summaries"),
    ),
    WorkflowCandidate(
        name="approval_wait_retry_flows",
        stage="later",
        summary="Pause on pending approval, resume after grant, then continue bounded execution.",
        temporal_fit="High; explicit wait states and resumable outcomes.",
        guardrails=("no self-approval", "ledger is source of truth", "resume via seam outcomes"),
    ),
    WorkflowCandidate(
        name="repo_maintenance_triage_loops",
        stage="later",
        summary="Repeated maintenance triage such as dirty worktree audits or failed-run clustering.",
        temporal_fit="High; periodic scans with low-latency operator synthesis.",
        guardrails=("read-only by default", "repo policy respected", "artifacts over prose"),
    ),
    WorkflowCandidate(
        name="specialist_wakeups",
        stage="later",
        summary="Wake bounded specialists when a condition is met, then route conclusions back through the operator.",
        temporal_fit="High; small callable organs fit a scheduler well.",
        guardrails=("bounded specialists only", "no risky approvals", "file-backed results"),
    ),
    WorkflowCandidate(
        name="gpu_provider_bringups_and_loop_runs",
        stage="later",
        summary="Coordinate heavier provider bringups, loop runs, or training orchestration when that substrate exists.",
        temporal_fit="Medium; worthwhile only once remote/GPU lanes stabilize.",
        guardrails=("policy-wrapped executors", "explicit approvals where risky", "durable receipts"),
    ),
)


def render_workflow_inventory() -> dict[str, Any]:
    return {
        "candidates": [candidate.to_dict() for candidate in WORKFLOW_CANDIDATES],
    }
