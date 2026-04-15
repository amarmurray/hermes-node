"""Shared operator turn runtime for terminal, Telegram, and future seams.

This is the single conversation loop for the operator. It keeps the model
backend, tool execution, session transcript, and natural-language synthesis
aligned across every ingress.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .backend import ChatBackend, ToolCall
from .constitution import build_system_prompt
from .response_synth import (
    SynthesisMode,
    SynthesisResult,
    ToolResultRef,
    synthesize_assistant_text,
)
from .session import append_message, build_messages_for_backend, read_notes
from .tools import ToolExecutor, get_tool_definitions


ToolCallHook = Callable[[ToolCall], None]
ToolResultHook = Callable[[str, str], None]
AssistantHook = Callable[[str], None]


@dataclass(frozen=True)
class ToolExecutionRecord:
    """One executed tool call within a turn."""

    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    raw_result: str
    preview: str
    success: bool = True


@dataclass
class TurnEffects:
    """Structured outcomes inferred from tool calls in one turn."""

    created_task_ids: list[str] = field(default_factory=list)
    task_ids_needing_approval: list[str] = field(default_factory=list)
    approved_task_ids: list[str] = field(default_factory=list)
    proposal_ids_written: list[str] = field(default_factory=list)
    subagent_ids: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    plan_touched: bool = False
    todo_touched: bool = False
    scratch_touched: bool = False

    @property
    def approval_needed(self) -> bool:
        return bool(self.task_ids_needing_approval)

    @property
    def task_created(self) -> bool:
        return bool(self.created_task_ids)

    @property
    def plan_or_task_created(self) -> bool:
        return self.plan_touched or self.task_created or bool(self.proposal_ids_written)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_task_ids": list(self.created_task_ids),
            "task_ids_needing_approval": list(self.task_ids_needing_approval),
            "approved_task_ids": list(self.approved_task_ids),
            "proposal_ids_written": list(self.proposal_ids_written),
            "subagent_ids": list(self.subagent_ids),
            "tool_names": list(self.tool_names),
            "plan_touched": self.plan_touched,
            "todo_touched": self.todo_touched,
            "scratch_touched": self.scratch_touched,
            "approval_needed": self.approval_needed,
            "task_created": self.task_created,
            "plan_or_task_created": self.plan_or_task_created,
        }


@dataclass(frozen=True)
class OperatorTurnResult:
    """Result of one operator turn after tool execution and synthesis."""

    session_id: str
    text: str
    raw_text: str
    mode: SynthesisMode
    synthesis_notes: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""
    finish_reason: str = ""
    tool_rounds: int = 0
    tool_limit_reached: bool = False
    tool_trace: tuple[ToolExecutionRecord, ...] = field(default_factory=tuple)
    effects: TurnEffects = field(default_factory=TurnEffects)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "text": self.text,
            "raw_text": self.raw_text,
            "mode": self.mode,
            "synthesis_notes": list(self.synthesis_notes),
            "error": self.error,
            "finish_reason": self.finish_reason,
            "tool_rounds": self.tool_rounds,
            "tool_limit_reached": self.tool_limit_reached,
            "tool_trace": [
                {
                    "tool_call_id": record.tool_call_id,
                    "name": record.name,
                    "arguments": record.arguments,
                    "preview": record.preview,
                    "success": record.success,
                }
                for record in self.tool_trace
            ],
            "effects": self.effects.to_dict(),
        }


def run_operator_turn(
    *,
    settings: Any,
    backend: ChatBackend,
    executor: ToolExecutor,
    repo_root: Path,
    session_id: str,
    user_input: str,
    tool_defs: list[dict[str, Any]] | None = None,
    response_mode: SynthesisMode = "natural",
    max_tool_rounds: int = 10,
    temperature: float = 0.3,
    on_intermediate_assistant: AssistantHook | None = None,
    on_tool_call: ToolCallHook | None = None,
    on_tool_result: ToolResultHook | None = None,
) -> OperatorTurnResult:
    """Execute one operator turn against the shared tool/runtime surface."""
    append_message(repo_root, session_id, role="user", content=user_input)

    notes = read_notes(repo_root, session_id)
    system_prompt = build_system_prompt(session_notes=notes)
    messages = build_messages_for_backend(
        repo_root,
        session_id,
        system_prompt=system_prompt,
    )
    tools = tool_defs if tool_defs is not None else get_tool_definitions()

    tool_refs: list[ToolResultRef] = []
    tool_trace: list[ToolExecutionRecord] = []
    effects = TurnEffects()
    finish_reason = ""

    rounds = 0
    while rounds < max_tool_rounds:
        rounds += 1
        try:
            response = backend.chat(messages, tools=tools, temperature=temperature)
        except Exception as exc:  # noqa: BLE001
            error = f"[backend error] {exc}"
            append_message(repo_root, session_id, role="assistant", content=error)
            return OperatorTurnResult(
                session_id=session_id,
                text=error,
                raw_text="",
                mode=response_mode,
                synthesis_notes=(),
                error=str(exc),
                tool_rounds=rounds,
                tool_trace=tuple(tool_trace),
                effects=effects,
            )

        finish_reason = response.finish_reason or finish_reason

        if response.tool_calls:
            tc_serialized = [_serialize_tool_call(tc) for tc in response.tool_calls]
            append_message(
                repo_root,
                session_id,
                role="assistant",
                content=response.content or "",
                tool_calls=tc_serialized,
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": tc_serialized,
                }
            )

            if response.content and on_intermediate_assistant:
                on_intermediate_assistant(response.content)

            for tc in response.tool_calls:
                effects.tool_names.append(tc.name)
                if on_tool_call:
                    on_tool_call(tc)

                result = executor.dispatch(tc.name, tc.arguments)
                preview = _tool_result_preview(tc.name, result)
                success = _tool_result_success(result)
                tool_refs.append(
                    ToolResultRef(
                        name=tc.name,
                        payload_preview=preview,
                        success=success,
                    )
                )
                tool_trace.append(
                    ToolExecutionRecord(
                        tool_call_id=tc.id,
                        name=tc.name,
                        arguments=tc.arguments,
                        raw_result=result,
                        preview=preview,
                        success=success,
                    )
                )
                _record_effects(effects, tc.name, result)

                append_message(
                    repo_root,
                    session_id,
                    role="tool",
                    content=result,
                    tool_call_id=tc.id,
                    name=tc.name,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result,
                    }
                )
                if on_tool_result:
                    on_tool_result(tc.name, result)
            continue

        synthesis = synthesize_assistant_text(
            response.content or "",
            mode=response_mode,
            tool_refs=tool_refs,
        )
        append_message(repo_root, session_id, role="assistant", content=synthesis.text)
        return OperatorTurnResult(
            session_id=session_id,
            text=synthesis.text,
            raw_text=response.content or "",
            mode=response_mode,
            synthesis_notes=synthesis.notes,
            finish_reason=finish_reason,
            tool_rounds=rounds,
            tool_trace=tuple(tool_trace),
            effects=effects,
        )

    fallback = synthesize_assistant_text(
        "",
        mode=response_mode,
        tool_refs=tool_refs,
        fallback=(
            "I hit the tool-call safety limit before I finished the write-up. "
            "The session transcript and tool receipts are intact if you want me "
            "to continue from there."
        ),
    )
    append_message(repo_root, session_id, role="assistant", content=fallback.text)
    return OperatorTurnResult(
        session_id=session_id,
        text=fallback.text,
        raw_text="",
        mode=response_mode,
        synthesis_notes=fallback.notes,
        finish_reason=finish_reason,
        tool_rounds=rounds,
        tool_limit_reached=True,
        tool_trace=tuple(tool_trace),
        effects=effects,
    )


def _serialize_tool_call(tc: ToolCall) -> dict[str, Any]:
    return {
        "id": tc.id,
        "type": "function",
        "function": {
            "name": tc.name,
            "arguments": json.dumps(tc.arguments),
        },
    }


def _tool_result_preview(name: str, raw_result: str) -> str:
    try:
        data = json.loads(raw_result)
    except json.JSONDecodeError:
        return raw_result.strip().splitlines()[0][:160]

    if isinstance(data, dict):
        if "error" in data:
            return str(data.get("error", ""))[:160]
        if data.get("summary"):
            return str(data["summary"]).strip().splitlines()[0][:160]
        if data.get("message"):
            return str(data["message"]).strip().splitlines()[0][:160]
        if data.get("content"):
            return str(data["content"]).strip().splitlines()[0][:160]
        if data.get("task_id") and data.get("state"):
            return f"{data['task_id']} [{data['state']}]"
        if data.get("task_id") and data.get("task_state"):
            return f"{data['task_id']} [{data['task_state']}]"
        if data.get("subagent_id"):
            return f"{data['subagent_id']} [{data.get('status', 'unknown')}]"
        keys = ", ".join(list(data.keys())[:5])
        return f"fields: {keys}"

    if isinstance(data, list):
        return f"{len(data)} item(s)"
    return str(data)[:160]


def _tool_result_success(raw_result: str) -> bool:
    try:
        data = json.loads(raw_result)
    except json.JSONDecodeError:
        return True
    if not isinstance(data, dict):
        return True
    if "error" in data:
        return False
    for key in ("submitted", "approved", "written", "updated", "completed", "saved"):
        if key in data and data.get(key) is False:
            return False
    if data.get("grounded") is False:
        return False
    return True


def _record_effects(effects: TurnEffects, name: str, raw_result: str) -> None:
    try:
        data = json.loads(raw_result)
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return

    if name == "submit_task" and data.get("submitted") and data.get("task_id"):
        task_id = str(data["task_id"])
        effects.created_task_ids.append(task_id)
        if data.get("approval_required") or data.get("state") == "pending_approval":
            effects.task_ids_needing_approval.append(task_id)
        return

    if name == "submit_patch_from_proposal" and data.get("submitted") and data.get("task_id"):
        task_id = str(data["task_id"])
        effects.created_task_ids.append(task_id)
        if data.get("approval_required") or data.get("task_state") == "pending_approval":
            effects.task_ids_needing_approval.append(task_id)
        proposal = data.get("proposal") or {}
        if isinstance(proposal, dict) and proposal.get("patch_id"):
            effects.proposal_ids_written.append(str(proposal["patch_id"]))
        return

    if name == "approve_task" and data.get("approved") and data.get("task_id"):
        effects.approved_task_ids.append(str(data["task_id"]))
        return

    if name in ("write_plan", "update_plan") and not data.get("error"):
        effects.plan_touched = True
        return

    if name in ("write_todo", "update_todo", "complete_todo_item") and not data.get("error"):
        effects.todo_touched = True
        return

    if name == "write_scratch" and not data.get("error"):
        effects.scratch_touched = True
        return

    if name == "spawn_subagent" and data.get("subagent_id"):
        effects.subagent_ids.append(str(data["subagent_id"]))
        return

    if name == "write_patch_proposal" and data.get("written"):
        proposal = data.get("proposal") or {}
        if isinstance(proposal, dict) and proposal.get("patch_id"):
            effects.proposal_ids_written.append(str(proposal["patch_id"]))

