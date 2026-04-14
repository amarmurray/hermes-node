"""Routing policy / execution constitution for hermes-node.

Maps task kinds to risk classes, executors, approval requirements, and
fallback paths.  This is the single source of truth for what is allowed
to run, how, and under what constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# -- Risk classes --

RISK_READ_ONLY = "read_only"
RISK_BRANCH_WRITE = "branch_write"
RISK_DESTRUCTIVE = "destructive"

ALL_RISK_CLASSES = (RISK_READ_ONLY, RISK_BRANCH_WRITE, RISK_DESTRUCTIVE)

# -- Executor names --

EXECUTOR_HERMES_CHAT = "hermes_chat"
EXECUTOR_CODEX = "codex"

ALL_EXECUTORS = (EXECUTOR_HERMES_CHAT, EXECUTOR_CODEX)


@dataclass(frozen=True)
class Route:
    """Routing policy for a single task kind."""

    kind: str
    risk_class: str
    default_executor: str
    fallback_executors: tuple[str, ...]
    approval_required: bool
    description: str


# -- Route table --
# This is the execution constitution.  Edit this table to change policy.

ROUTE_TABLE: dict[str, Route] = {
    "summary": Route(
        kind="summary",
        risk_class=RISK_READ_ONLY,
        default_executor=EXECUTOR_HERMES_CHAT,
        fallback_executors=(),
        approval_required=False,
        description="Cheap summary via default OpenRouter model. Read-only.",
    ),
    "research": Route(
        kind="research",
        risk_class=RISK_READ_ONLY,
        default_executor=EXECUTOR_HERMES_CHAT,
        fallback_executors=(),
        approval_required=False,
        description="Web research via OpenRouter. Read-only, no host mutation.",
    ),
    "repo_read": Route(
        kind="repo_read",
        risk_class=RISK_READ_ONLY,
        default_executor=EXECUTOR_CODEX,
        fallback_executors=(EXECUTOR_HERMES_CHAT,),
        approval_required=False,
        description="Read-only repo inspection. Codex preferred, hermes_chat fallback.",
    ),
    "repo_patch_branch": Route(
        kind="repo_patch_branch",
        risk_class=RISK_BRANCH_WRITE,
        default_executor=EXECUTOR_CODEX,
        fallback_executors=(),
        approval_required=True,
        description=(
            "Code changes on a named branch. Codex only. "
            "Requires explicit approval. Never writes to main."
        ),
    ),
}

# Destructive kinds are not in the table — they are blocked.
# To add one later, add it here with RISK_DESTRUCTIVE and approval_required=True.


def get_route(kind: str) -> Route:
    """Return the route for a task kind, or raise ValueError."""
    try:
        return ROUTE_TABLE[kind]
    except KeyError as exc:
        raise ValueError(f"No route defined for task kind: {kind}") from exc


def resolve_executor(kind: str, *, available_executors: set[str]) -> str | None:
    """Pick the best available executor for a task kind.

    Returns the executor name, or None if no executor is available.
    """
    route = get_route(kind)
    if route.default_executor in available_executors:
        return route.default_executor
    for fallback in route.fallback_executors:
        if fallback in available_executors:
            return fallback
    return None


def is_approval_required(kind: str) -> bool:
    """Check if a task kind requires explicit approval per policy."""
    return get_route(kind).approval_required


def is_kind_allowed(kind: str) -> bool:
    """Check if a task kind has a route (i.e. is not blocked)."""
    return kind in ROUTE_TABLE


def format_route_table() -> list[dict[str, Any]]:
    """Return the full route table as a list of dicts for display."""
    return [
        {
            "kind": r.kind,
            "risk_class": r.risk_class,
            "default_executor": r.default_executor,
            "fallback_executors": list(r.fallback_executors),
            "approval_required": r.approval_required,
            "description": r.description,
        }
        for r in ROUTE_TABLE.values()
    ]
