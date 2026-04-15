"""Classification of upstream Hermes capabilities relative to the membrane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HermesCapability:
    name: str
    status: str
    summary: str
    membrane_path: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "membrane_path": self.membrane_path,
            "note": self.note,
        }


CAPABILITIES: tuple[HermesCapability, ...] = (
    HermesCapability(
        name="upstream_cli_runtime",
        status="wrapped",
        summary="Canonical upstream Hermes runtime remains the execution substrate.",
        membrane_path="hermes-node wrapper -> pinned upstream CLI",
    ),
    HermesCapability(
        name="provider_model_selection",
        status="wrapped",
        summary="Provider and model selection happen through hermes-node task submission and operator policy.",
        membrane_path="ledger task metadata + operator backend policy",
    ),
    HermesCapability(
        name="read_only_repo_execution",
        status="wrapped",
        summary="Read-first repo work is surfaced through hermes-node policy, routing, and receipts.",
        membrane_path="repo-aware tools + ledger/workspaces",
    ),
    HermesCapability(
        name="session_and_receipt_artifacts",
        status="wrapped",
        summary="Operator sessions, task workspaces, and run receipts are durable and inspectable.",
        membrane_path="state/operator_sessions + state/workspaces",
    ),
    HermesCapability(
        name="local_only_gateway",
        status="candidate",
        summary="A local-only API ingress may later call the same operator seam for Temporal or Mini App use.",
        membrane_path="future local seam, not public exposure",
    ),
    HermesCapability(
        name="bounded_monitor_loops",
        status="candidate",
        summary="Upstream monitoring loops are candidates only when wrapped by hermes-node policy and receipts.",
        membrane_path="future Temporal/operator scheduling seam",
    ),
    HermesCapability(
        name="additional_executor_lanes",
        status="candidate",
        summary="Claude or specialist lanes can sit under hermes-node once routing and approvals cover them.",
        membrane_path="routing policy + approval gates",
    ),
    HermesCapability(
        name="public_gateway_exposure",
        status="withheld",
        summary="No public API or gateway exposure in the current private-first posture.",
        note="Keep ingress private over SSH/Tailscale only.",
    ),
    HermesCapability(
        name="direct_temporal_replacement",
        status="withheld",
        summary="Hermes-node is not a replacement for Temporal orchestration.",
        note="Temporal sits above the seam later.",
    ),
    HermesCapability(
        name="unbounded_repo_mutation",
        status="withheld",
        summary="Repo mutation remains gated by proposals, routing, approvals, and branch policy.",
        note="No direct main-branch mutation.",
    ),
    HermesCapability(
        name="autonomous_capability_activation_without_policy",
        status="withheld",
        summary="Upstream capability breadth does not become autonomous behavior unless the membrane wraps it first.",
    ),
)


def render_hermes_capability_registry() -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {"wrapped": [], "candidate": [], "withheld": []}
    for capability in CAPABILITIES:
        grouped.setdefault(capability.status, []).append(capability.to_dict())
    return grouped

