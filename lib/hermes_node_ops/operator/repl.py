"""Operator terminal REPL — interactive chat with the operator LLM.

The REPL handles:
- Readline-based input with history
- Conversation loop with tool calling
- Session persistence
- Graceful error handling for backend failures
"""

from __future__ import annotations

import json
import readline
import sys
from pathlib import Path
from typing import Any

from ..config import Settings
from .backend import AssistantMessage, ChatBackend, ToolCall
from .constitution import build_system_prompt
from .session import (
    append_message,
    build_messages_for_backend,
    create_session,
    read_notes,
)
from .tools import ToolExecutor, get_tool_definitions


MAX_TOOL_ROUNDS = 10  # safety limit on consecutive tool call rounds


def run_repl(
    settings: Settings,
    backend: ChatBackend,
    *,
    session_id: str = "",
) -> None:
    """Run the interactive operator REPL."""
    repo_root = settings.repo_root
    sid = create_session(repo_root, model=backend.model_name, resume=session_id)
    executor = ToolExecutor(settings, repo_root, session_id=sid)

    print(f"hermes-node operator ({backend.model_name})")
    print(f"session: {sid}")
    print("type /quit to exit, /status for queue, /sessions to list, /notes to view notes")
    print()

    # Set up readline history
    history_path = repo_root / "state" / "operator_sessions" / sid / ".repl_history"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        readline.read_history_file(str(history_path))
    except (FileNotFoundError, OSError):
        pass

    tool_defs = get_tool_definitions()

    try:
        while True:
            try:
                user_input = input("\x1b[1;36moperator>\x1b[0m ").strip()
            except EOFError:
                print()
                break

            if not user_input:
                continue

            # Slash commands
            if user_input.startswith("/"):
                handled = _handle_slash_command(user_input, settings, repo_root, sid)
                if handled == "quit":
                    break
                continue

            # Record user message
            append_message(repo_root, sid, role="user", content=user_input)

            # Build messages and call backend
            notes = read_notes(repo_root, sid)
            system_prompt = build_system_prompt(session_notes=notes)
            messages = build_messages_for_backend(
                repo_root, sid, system_prompt=system_prompt,
            )

            _conversation_turn(
                backend=backend,
                executor=executor,
                messages=messages,
                tool_defs=tool_defs,
                repo_root=repo_root,
                session_id=sid,
            )

    except KeyboardInterrupt:
        print("\n\nInterrupted.")
    finally:
        try:
            readline.write_history_file(str(history_path))
        except OSError:
            pass
        print(f"\nsession saved: {sid}")


def _conversation_turn(
    *,
    backend: ChatBackend,
    executor: ToolExecutor,
    messages: list[dict[str, Any]],
    tool_defs: list[dict[str, Any]],
    repo_root: Path,
    session_id: str,
) -> None:
    """Execute one conversation turn, handling tool call loops."""
    rounds = 0
    while rounds < MAX_TOOL_ROUNDS:
        rounds += 1

        try:
            response = backend.chat(messages, tools=tool_defs)
        except Exception as exc:
            error_msg = f"[backend error: {exc}]"
            print(f"\x1b[31m{error_msg}\x1b[0m")
            append_message(repo_root, session_id, role="assistant", content=error_msg)
            return

        # If there are tool calls, execute them
        if response.tool_calls:
            # Record assistant message with tool calls
            tc_serialized = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in response.tool_calls
            ]
            append_message(
                repo_root, session_id,
                role="assistant",
                content=response.content or "",
                tool_calls=tc_serialized,
            )
            messages.append({
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": tc_serialized,
            })

            if response.content:
                print(f"\x1b[2m{response.content}\x1b[0m")

            # Execute each tool call
            for tc in response.tool_calls:
                _print_tool_call(tc)
                result = executor.dispatch(tc.name, tc.arguments)
                _print_tool_result(tc.name, result)

                # Record tool result
                append_message(
                    repo_root, session_id,
                    role="tool",
                    content=result,
                    tool_call_id=tc.id,
                    name=tc.name,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": result,
                })

            # Continue the loop so the model can process tool results
            continue

        # No tool calls — just text response
        if response.content:
            print(f"\n\x1b[1;37m{response.content}\x1b[0m\n")
            append_message(repo_root, session_id, role="assistant", content=response.content)
        return

    print("\x1b[33m[tool call limit reached]\x1b[0m")


def _print_tool_call(tc: ToolCall) -> None:
    """Print a tool call for the user to see."""
    args_str = ", ".join(f"{k}={json.dumps(v)}" for k, v in tc.arguments.items())
    print(f"  \x1b[33m-> {tc.name}({args_str})\x1b[0m")


def _print_tool_result(name: str, result: str) -> None:
    """Print a condensed tool result."""
    # Try to parse as JSON for nicer display
    try:
        data = json.loads(result)
        if isinstance(data, dict) and "error" in data:
            print(f"  \x1b[31m   error: {data['error']}\x1b[0m")
            return
        # Just show a brief summary
        if isinstance(data, dict):
            keys = list(data.keys())
            print(f"  \x1b[2m   [{', '.join(keys[:5])}{'...' if len(keys) > 5 else ''}]\x1b[0m")
        elif isinstance(data, list):
            print(f"  \x1b[2m   [{len(data)} items]\x1b[0m")
        else:
            preview = result[:100]
            print(f"  \x1b[2m   {preview}{'...' if len(result) > 100 else ''}\x1b[0m")
    except json.JSONDecodeError:
        preview = result[:100]
        print(f"  \x1b[2m   {preview}{'...' if len(result) > 100 else ''}\x1b[0m")


def _handle_slash_command(
    command: str,
    settings: Settings,
    repo_root: Path,
    session_id: str,
) -> str:
    """Handle a slash command. Returns 'quit' to exit, '' otherwise."""
    parts = command.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/quit", "/exit", "/q"):
        return "quit"

    if cmd == "/status":
        from ..ledger import Ledger
        from ..wrapper import format_status_snapshot
        with Ledger(settings.ledger_db) as ledger:
            snap = format_status_snapshot(ledger, limit=5)
        counts = snap.get("counts", {})
        print("queue:", " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        deferred = snap.get("deferred_tasks", [])
        if deferred:
            print(f"deferred: {len(deferred)} tasks")
        cooldowns = snap.get("active_cooldowns", [])
        if cooldowns:
            print(f"cooldowns: {len(cooldowns)} active")
        return ""

    if cmd == "/sessions":
        from .session import list_sessions
        sessions = list_sessions(repo_root, limit=10)
        if not sessions:
            print("no sessions")
        for s in sessions:
            sid = s.get("session_id", "?")
            msgs = s.get("message_count", 0)
            model = s.get("model", "?")
            last = s.get("last_active", "?")
            marker = " <-" if sid == session_id else ""
            print(f"  {sid}  msgs={msgs}  model={model}  last={last}{marker}")
        return ""

    if cmd == "/notes":
        notes = read_notes(repo_root, session_id)
        if notes.strip():
            print(notes)
        else:
            print("(no notes)")
        return ""

    if cmd == "/note" and arg:
        from .session import append_note
        append_note(repo_root, session_id, arg)
        print("noted.")
        return ""

    if cmd == "/help":
        print("commands: /quit /status /sessions /notes /note <text> /help")
        return ""

    print(f"unknown command: {cmd}")
    return ""
