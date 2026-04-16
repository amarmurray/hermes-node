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
from pathlib import Path

from ..config import Settings
from .backend import ChatBackend, ToolCall
from .harness import (
    list_subagents,
    read_plan,
    read_todo_markdown,
)
from .harness.specialists import SPECIALIST_ROLES
from .project_memory import detect_project, render_memory_layers
from .runtime import run_operator_turn
from .session import append_note, create_session, read_notes
from .temporal_seam import SpecialistInvocation, invoke_specialist_once
from .tools import ToolExecutor, get_tool_definitions


MAX_TOOL_ROUNDS = 10  # safety limit on consecutive tool call rounds

# Module-level verbose flag toggled by /verbose. Default is quiet —
# the user sees only the final synthesized answer.
_verbose_mode = False


def run_repl(
    settings: Settings,
    backend: ChatBackend,
    *,
    session_id: str = "",
) -> None:
    """Run the interactive operator REPL."""
    global _verbose_mode  # noqa: PLW0603
    _verbose_mode = False

    repo_root = settings.repo_root
    project_ctx = detect_project()
    project = (
        {
            "repo_root": project_ctx.repo_root,
            "branch": project_ctx.branch,
            "worktree": project_ctx.worktree,
            "repo_name": project_ctx.repo_name,
            "cwd": project_ctx.cwd,
        }
        if project_ctx.in_git_repo
        else None
    )
    sid = create_session(
        repo_root,
        model=backend.model_name,
        resume=session_id,
        project=project,
    )
    executor = ToolExecutor(settings, repo_root, session_id=sid)

    print(f"hermes-node operator ({backend.model_name})")
    print(f"session: {sid}")
    print("type /quit to exit, /verbose to toggle debug output")
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

            # Record user message — hooks only fire in verbose mode
            result = run_operator_turn(
                settings=settings,
                backend=backend,
                executor=executor,
                repo_root=repo_root,
                session_id=sid,
                user_input=user_input,
                tool_defs=tool_defs,
                max_tool_rounds=MAX_TOOL_ROUNDS,
                on_intermediate_assistant=_print_intermediate_assistant if _verbose_mode else None,
                on_tool_call=_print_tool_call if _verbose_mode else None,
                on_tool_result=_print_tool_result if _verbose_mode else None,
            )
            if result.error:
                print(f"\x1b[31m{result.text}\x1b[0m")
                continue
            print(f"\n\x1b[1;37m{result.text}\x1b[0m\n")
            if result.tool_limit_reached:
                print("\x1b[33m[tool call limit reached]\x1b[0m")

    except KeyboardInterrupt:
        print("\n\nInterrupted.")
    finally:
        try:
            readline.write_history_file(str(history_path))
        except OSError:
            pass
        print(f"\nsession saved: {sid}")


def _print_intermediate_assistant(content: str) -> None:
    if content.strip():
        print(f"\x1b[2m{content}\x1b[0m")


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
        repo_filter = ""
        tokens = arg.split()
        if "--repo" in tokens:
            idx = tokens.index("--repo")
            if idx + 1 < len(tokens):
                repo_filter = tokens[idx + 1]
        sessions = list_sessions(
            repo_root,
            limit=10,
            project_repo_name=repo_filter,
        )
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

    if cmd == "/plan":
        plan = read_plan(repo_root, session_id).strip()
        print(plan if plan else "(no plan)")
        return ""

    if cmd == "/todo":
        todo = read_todo_markdown(repo_root, session_id).strip()
        print(todo if todo else "(no todo)")
        return ""

    if cmd == "/subagents":
        subagents = list_subagents(repo_root, session_id)
        if not subagents:
            print("(no subagents)")
            return ""
        for subagent in subagents[:10]:
            print(
                f"  {subagent.get('subagent_id','?')} "
                f"[{subagent.get('status','?')}] "
                f"{subagent.get('role','?')} "
                f"{subagent.get('goal','')}"
            )
        return ""

    if cmd == "/note" and arg:
        append_note(repo_root, session_id, arg)
        print("noted.")
        return ""

    if cmd == "/whereami":
        ctx = detect_project()
        print(f"  cwd:         {ctx.cwd}")
        print(f"  repo_root:   {ctx.repo_root or '(not in git repo)'}")
        print(f"  branch:      {ctx.branch or '(n/a)'}")
        print(f"  repo_name:   {ctx.repo_name or '(n/a)'}")
        print(f"  memory:      {len(ctx.memory_layers)} layer(s)")
        return ""

    if cmd == "/project":
        ctx = detect_project()
        print(f"  cwd:         {ctx.cwd}")
        print(f"  repo_root:   {ctx.repo_root or '(not in git repo)'}")
        print(f"  branch:      {ctx.branch or '(n/a)'}")
        print(f"  repo_name:   {ctx.repo_name or '(n/a)'}")
        print(f"  memory:      {len(ctx.memory_layers)} layer(s)")
        rendered = render_memory_layers(ctx.memory_layers)
        if rendered:
            lines = rendered.splitlines()
            for line in lines[:40]:
                print(line)
            if len(lines) > 40:
                print("… (truncated)")
        return ""

    if cmd == "/remember" and arg:
        ctx = detect_project()
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if ctx.repo_root:
            agents_path = Path(ctx.repo_root) / "AGENTS.md"
            try:
                existing = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                agents_path.write_text(
                    existing + f"- [{stamp}] {arg}\n",
                    encoding="utf-8",
                )
                print(f"remembered in {agents_path}")
            except OSError as exc:
                print(f"failed to write AGENTS.md: {exc}")
        else:
            append_note(repo_root, session_id, arg)
            print("not in a git repo — remembered in session notes")
        return ""

    if cmd == "/specialist" and arg:
        parts2 = arg.split(maxsplit=1)
        role = parts2[0]
        goal = parts2[1] if len(parts2) > 1 else ""
        if role not in SPECIALIST_ROLES:
            print(f"unknown role: {role}")
            print(f"available: {', '.join(SPECIALIST_ROLES)}")
            return ""
        ctx = detect_project()
        outcome = invoke_specialist_once(
            settings,
            SpecialistInvocation(
                role=role,
                goal=goal,
                parent_session_id=session_id,
                repo_path=ctx.repo_root or "",
            ),
        )
        if outcome.error:
            print(f"specialist error: {outcome.error}")
        if outcome.report:
            print(outcome.report)
        if outcome.subagent_id:
            print(f"[subagent: {outcome.subagent_id}]")
        return ""

    if cmd == "/verbose":
        global _verbose_mode  # noqa: PLW0603
        _verbose_mode = not _verbose_mode
        state = "on" if _verbose_mode else "off"
        print(f"verbose mode: {state}")
        return ""

    if cmd == "/help":
        print(
            "commands: /quit /status /sessions [--repo <name>] /notes /plan /todo "
            "/subagents /note <text> /whereami /project /remember <text> "
            "/specialist <role> <goal...> /verbose /help"
        )
        return ""

    print(f"unknown command: {cmd}")
    return ""
