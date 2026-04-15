"""Future Temporal seam for invoking the operator without adding Temporal now.

This is a thin, local interface: future workflow engines can call it to run
one operator turn, inspect structured outcomes, and decide whether a durable
workflow step should continue, pause for approval, or hand control back to
the human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from .backend import ChatBackend
from .backend_policy import OperatorIntent, resolve_backend
from .runtime import OperatorTurnResult, run_operator_turn
from .session import create_session
from .tools import ToolExecutor


@dataclass(frozen=True)
class OperatorInvocation:
    """Input envelope for a single operator invocation."""

    goal: str
    session_id: str = ""
    context: str = ""
    intent: OperatorIntent | str = OperatorIntent.PLAN
    response_mode: str = "natural"
    max_tool_rounds: int = 10
    temperature: float = 0.3

    def render_user_input(self) -> str:
        goal = self.goal.strip()
        context = self.context.strip()
        if not context:
            return goal
        return f"{goal}\n\nContext:\n{context}"


@dataclass(frozen=True)
class OperatorOutcome:
    """Structured result suitable for a future scheduler/orchestrator."""

    session_id: str
    assistant_text: str
    raw_assistant_text: str
    intent: str
    response_mode: str
    backend_model: str
    backend_slot: str = ""
    backend_reason: str = ""
    error: str = ""
    finish_reason: str = ""
    tool_names: tuple[str, ...] = field(default_factory=tuple)
    task_ids_created: tuple[str, ...] = field(default_factory=tuple)
    task_ids_needing_approval: tuple[str, ...] = field(default_factory=tuple)
    approved_task_ids: tuple[str, ...] = field(default_factory=tuple)
    proposal_ids_written: tuple[str, ...] = field(default_factory=tuple)
    subagent_ids: tuple[str, ...] = field(default_factory=tuple)
    plan_touched: bool = False
    todo_touched: bool = False
    scratch_touched: bool = False
    tool_limit_reached: bool = False
    synthesis_notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def approval_needed(self) -> bool:
        return bool(self.task_ids_needing_approval)

    @property
    def task_created(self) -> bool:
        return bool(self.task_ids_created)

    @property
    def plan_or_task_created(self) -> bool:
        return self.plan_touched or self.task_created or bool(self.proposal_ids_written)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "assistant_text": self.assistant_text,
            "raw_assistant_text": self.raw_assistant_text,
            "intent": self.intent,
            "response_mode": self.response_mode,
            "backend_model": self.backend_model,
            "backend_slot": self.backend_slot,
            "backend_reason": self.backend_reason,
            "error": self.error,
            "finish_reason": self.finish_reason,
            "tool_names": list(self.tool_names),
            "task_ids_created": list(self.task_ids_created),
            "task_ids_needing_approval": list(self.task_ids_needing_approval),
            "approved_task_ids": list(self.approved_task_ids),
            "proposal_ids_written": list(self.proposal_ids_written),
            "subagent_ids": list(self.subagent_ids),
            "plan_touched": self.plan_touched,
            "todo_touched": self.todo_touched,
            "scratch_touched": self.scratch_touched,
            "tool_limit_reached": self.tool_limit_reached,
            "synthesis_notes": list(self.synthesis_notes),
            "approval_needed": self.approval_needed,
            "task_created": self.task_created,
            "plan_or_task_created": self.plan_or_task_created,
        }


def invoke_operator_once(
    settings: Settings,
    backend: ChatBackend,
    invocation: OperatorInvocation,
) -> OperatorOutcome:
    """Invoke the operator once against the existing tool/session surface."""
    repo_root = Path(settings.repo_root)
    session_id = create_session(
        repo_root,
        model=backend.model_name,
        resume=invocation.session_id,
    )
    executor = ToolExecutor(settings, repo_root, session_id=session_id)
    turn = run_operator_turn(
        settings=settings,
        backend=backend,
        executor=executor,
        repo_root=repo_root,
        session_id=session_id,
        user_input=invocation.render_user_input(),
        response_mode=invocation.response_mode,
        max_tool_rounds=invocation.max_tool_rounds,
        temperature=invocation.temperature,
    )
    return _outcome_from_turn(
        turn,
        intent=str(OperatorIntent(invocation.intent).value),
        backend_model=backend.model_name,
    )


def invoke_operator_with_policy(
    settings: Settings,
    invocation: OperatorInvocation,
    *,
    model_override: str = "",
    base_url_override: str = "",
    api_key_override: str = "",
    timeout_seconds: int = 0,
) -> OperatorOutcome:
    """Resolve a backend through policy, then invoke the operator once."""
    backend, selection = resolve_backend(
        settings,
        intent=invocation.intent,
        model_override=model_override,
        base_url_override=base_url_override,
        api_key_override=api_key_override,
        timeout_seconds=timeout_seconds,
    )
    outcome = invoke_operator_once(settings, backend, invocation)
    return OperatorOutcome(
        session_id=outcome.session_id,
        assistant_text=outcome.assistant_text,
        raw_assistant_text=outcome.raw_assistant_text,
        intent=outcome.intent,
        response_mode=outcome.response_mode,
        backend_model=outcome.backend_model,
        backend_slot=selection.descriptor.slot.value,
        backend_reason=selection.reason,
        error=outcome.error,
        finish_reason=outcome.finish_reason,
        tool_names=outcome.tool_names,
        task_ids_created=outcome.task_ids_created,
        task_ids_needing_approval=outcome.task_ids_needing_approval,
        approved_task_ids=outcome.approved_task_ids,
        proposal_ids_written=outcome.proposal_ids_written,
        subagent_ids=outcome.subagent_ids,
        plan_touched=outcome.plan_touched,
        todo_touched=outcome.todo_touched,
        scratch_touched=outcome.scratch_touched,
        tool_limit_reached=outcome.tool_limit_reached,
        synthesis_notes=outcome.synthesis_notes,
    )


def _outcome_from_turn(
    turn: OperatorTurnResult,
    *,
    intent: str,
    backend_model: str,
) -> OperatorOutcome:
    return OperatorOutcome(
        session_id=turn.session_id,
        assistant_text=turn.text,
        raw_assistant_text=turn.raw_text,
        intent=intent,
        response_mode=turn.mode,
        backend_model=backend_model,
        error=turn.error,
        finish_reason=turn.finish_reason,
        tool_names=tuple(turn.effects.tool_names),
        task_ids_created=tuple(turn.effects.created_task_ids),
        task_ids_needing_approval=tuple(turn.effects.task_ids_needing_approval),
        approved_task_ids=tuple(turn.effects.approved_task_ids),
        proposal_ids_written=tuple(turn.effects.proposal_ids_written),
        subagent_ids=tuple(turn.effects.subagent_ids),
        plan_touched=turn.effects.plan_touched,
        todo_touched=turn.effects.todo_touched,
        scratch_touched=turn.effects.scratch_touched,
        tool_limit_reached=turn.tool_limit_reached,
        synthesis_notes=tuple(turn.synthesis_notes),
    )

