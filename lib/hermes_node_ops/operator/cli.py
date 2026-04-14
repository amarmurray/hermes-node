"""CLI entry point for the hermes-node operator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import Settings
from .backend import create_backend
from .repl import run_repl
from .session import list_sessions, read_context, read_notes, read_thread


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.load(repo_root=Path(__file__).resolve().parents[3])
    settings.ensure_wrapper_dirs()

    if args.command == "chat":
        return _handle_chat(settings, args)

    if args.command == "sessions":
        return _handle_sessions(settings, args)

    if args.command == "thread":
        return _handle_thread(settings, args)

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-operator",
        description="Operator LLM — conversational foreman for hermes-node",
    )
    subparsers = parser.add_subparsers(dest="command")

    chat_parser = subparsers.add_parser(
        "chat",
        help="Start an interactive operator chat session",
    )
    chat_parser.add_argument(
        "--model", default="",
        help="Model name for the backend (e.g. qwen3:1.7b)",
    )
    chat_parser.add_argument(
        "--base-url", default="",
        help="Backend API base URL (default: http://localhost:11434 for Ollama)",
    )
    chat_parser.add_argument(
        "--api-key", default="",
        help="API key for the backend (empty for local servers)",
    )
    chat_parser.add_argument(
        "--resume", default="",
        help="Resume an existing session by ID",
    )
    chat_parser.add_argument(
        "--timeout", type=int, default=120,
        help="Backend request timeout in seconds",
    )

    sessions_parser = subparsers.add_parser(
        "sessions",
        help="List operator sessions",
    )
    sessions_parser.add_argument("--limit", type=int, default=20)
    sessions_parser.add_argument("--json", action="store_true")

    thread_parser = subparsers.add_parser(
        "thread",
        help="View a session's conversation thread",
    )
    thread_parser.add_argument("session_id", help="Session ID to inspect")
    thread_parser.add_argument("--limit", type=int, default=50)
    thread_parser.add_argument("--json", action="store_true")
    thread_parser.add_argument("--notes", action="store_true", help="Show notes instead of thread")

    return parser


def _handle_chat(settings: Settings, args: argparse.Namespace) -> int:
    import os
    base_url = (
        args.base_url
        or os.environ.get("HERMES_OPERATOR_BASE_URL", "")
        or "http://localhost:11434"
    )
    api_key = (
        args.api_key
        or os.environ.get("HERMES_OPERATOR_API_KEY", "")
    )
    model = (
        args.model
        or os.environ.get("HERMES_OPERATOR_MODEL", "")
        or "qwen3:1.7b"
    )

    backend = create_backend(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=args.timeout,
    )

    # Check backend availability
    if not backend.available():
        print(f"Backend not reachable at {base_url}")
        print(f"Make sure your inference server is running.")
        print(f"  Ollama:    ollama serve && ollama pull {model}")
        print(f"  llama.cpp: ./server -m model.gguf --port 8080")
        print(f"  vLLM:      vllm serve {model}")
        return 1

    run_repl(settings, backend, session_id=args.resume)
    return 0


def _handle_sessions(settings: Settings, args: argparse.Namespace) -> int:
    sessions = list_sessions(settings.repo_root, limit=args.limit)
    if args.json:
        print(json.dumps(sessions, indent=2, sort_keys=True))
        return 0

    if not sessions:
        print("no sessions")
        return 0

    for s in sessions:
        sid = s.get("session_id", "?")
        msgs = s.get("message_count", 0)
        model = s.get("model", "?")
        last = s.get("last_active", "?")
        print(f"  {sid}  msgs={msgs}  model={model}  last={last}")
    return 0


def _handle_thread(settings: Settings, args: argparse.Namespace) -> int:
    repo_root = settings.repo_root

    if args.notes:
        notes = read_notes(repo_root, args.session_id)
        print(notes if notes.strip() else "(no notes)")
        return 0

    thread = read_thread(repo_root, args.session_id, limit=args.limit)
    if args.json:
        print(json.dumps(thread, indent=2, sort_keys=True))
        return 0

    if not thread:
        print("(empty thread)")
        return 0

    for entry in thread:
        role = entry.get("role", "?")
        content = entry.get("content", "")
        ts = entry.get("timestamp", "")

        if role == "user":
            print(f"\x1b[1;36m[{ts}] user:\x1b[0m {content}")
        elif role == "assistant":
            tc = entry.get("tool_calls", [])
            if tc:
                print(f"\x1b[1;33m[{ts}] assistant (tool calls):\x1b[0m")
                for call in tc:
                    func = call.get("function", {})
                    print(f"  -> {func.get('name', '?')}({func.get('arguments', '{}')})")
            if content:
                print(f"\x1b[1;37m[{ts}] assistant:\x1b[0m {content}")
        elif role == "tool":
            name = entry.get("name", "?")
            preview = content[:200]
            print(f"\x1b[2m[{ts}] tool/{name}: {preview}{'...' if len(content) > 200 else ''}\x1b[0m")
        else:
            print(f"[{ts}] {role}: {content}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
