from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


TASK_STATE_PENDING = "pending"
TASK_STATE_PENDING_APPROVAL = "pending_approval"
TASK_STATE_RUNNING = "running"
TASK_STATE_DONE = "done"
TASK_STATE_FAILED = "failed"
TASK_STATE_REJECTED = "rejected"
TASK_STATE_DEFERRED_RATE_LIMIT = "deferred_rate_limit"

# Backwards-compat aliases for migration period
TASK_STATE_WAITING_APPROVAL = TASK_STATE_PENDING_APPROVAL
TASK_STATE_COMPLETED = TASK_STATE_DONE

# State renames applied during schema migration
_STATE_RENAMES = {
    "waiting_approval": "pending_approval",
    "completed": "done",
}

# Default retry/backoff policy
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_BACKOFF_SECONDS = 30
DEFAULT_MAX_BACKOFF_SECONDS = 900  # 15 minutes

APPROVAL_NOT_REQUIRED = "not_required"
APPROVAL_REQUESTED = "requested"
APPROVAL_GRANTED = "granted"
APPROVAL_REJECTED = "rejected"


def new_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid4().hex[:8]}"


@dataclass(frozen=True)
class TaskProfile:
    kind: str
    toolsets: tuple[str, ...]
    default_max_turns: int
    prompt_preamble: str
    requires_repo_path: bool = False
    use_worktree: bool = False


TASK_PROFILES: dict[str, TaskProfile] = {
    "summary": TaskProfile(
        kind="summary",
        toolsets=("clarify", "memory", "session_search"),
        default_max_turns=2,
        prompt_preamble=(
            "You are operating in hermes-node summary mode. "
            "Produce a concise operator-facing summary. "
            "Do not write files, patch files, or run shell commands."
        ),
    ),
    "research": TaskProfile(
        kind="research",
        toolsets=("web", "clarify", "memory", "session_search"),
        default_max_turns=4,
        prompt_preamble=(
            "You are operating in hermes-node research mode. "
            "Use web research when needed. "
            "Do not modify repositories, files, or host state."
        ),
    ),
    "repo_read": TaskProfile(
        kind="repo_read",
        toolsets=("file", "clarify", "memory", "session_search"),
        default_max_turns=4,
        prompt_preamble=(
            "You are operating in hermes-node repo_read mode. "
            "Inspect the repository read-only. "
            "Do not write files, patch files, stage changes, commit, or run shell mutations. "
            "If the answer would require mutation, say so plainly."
        ),
        requires_repo_path=True,
        use_worktree=True,
    ),
}

SUPPORTED_TASK_KINDS = tuple(sorted(TASK_PROFILES))


def get_task_profile(kind: str) -> TaskProfile:
    try:
        return TASK_PROFILES[kind]
    except KeyError as exc:
        raise ValueError(f"Unsupported task kind: {kind}") from exc


@dataclass
class TaskSubmission:
    kind: str
    prompt: str
    title: str = ""
    provider: str = ""
    model: str = ""
    repo_path: str = ""
    require_approval: bool = False
    created_by: str = "operator"
    max_turns: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionOutcome:
    task_id: str
    run_id: str | None
    state: str
    exit_code: int | None
    summary: str
    stdout: str = ""
    stderr: str = ""
