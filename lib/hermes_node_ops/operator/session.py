"""Operator session persistence — durable conversation threads and notes.

Sessions are stored as directories under state/operator_sessions/{session_id}/:
  - thread.jsonl  — append-only conversation log
  - notes.md      — persistent scratch notes
  - context.json  — session metadata

All artifacts are plain files, inspectable with cat/jq, crash-safe via
JSONL append. No database table needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..jsonl import utcnow
from ..models import new_id


def sessions_root(repo_root: Path) -> Path:
    return repo_root / "state" / "operator_sessions"


def session_dir(repo_root: Path, session_id: str) -> Path:
    return sessions_root(repo_root) / session_id


def create_session(repo_root: Path, *, model: str = "", resume: str = "") -> str:
    """Create a new session or resume an existing one.

    If resume is a valid session ID that exists, returns it.
    Otherwise creates a new session directory.
    """
    if resume:
        existing = session_dir(repo_root, resume)
        if existing.exists():
            _update_context(existing, last_active=utcnow())
            return resume

    sid = new_id("session")
    sdir = session_dir(repo_root, sid)
    sdir.mkdir(parents=True, exist_ok=True)

    context = {
        "session_id": sid,
        "created_at": utcnow(),
        "last_active": utcnow(),
        "model": model,
    }
    (sdir / "context.json").write_text(
        json.dumps(context, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (sdir / "notes.md").write_text(
        f"# Session {sid}\n\nNotes:\n\n",
        encoding="utf-8",
    )
    return sid


def append_message(
    repo_root: Path,
    session_id: str,
    *,
    role: str,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_call_id: str = "",
    name: str = "",
) -> None:
    """Append a message to the session thread."""
    sdir = session_dir(repo_root, session_id)
    sdir.mkdir(parents=True, exist_ok=True)

    entry: dict[str, Any] = {
        "timestamp": utcnow(),
        "role": role,
        "content": content,
    }
    if tool_calls:
        entry["tool_calls"] = tool_calls
    if tool_call_id:
        entry["tool_call_id"] = tool_call_id
    if name:
        entry["name"] = name

    thread_path = sdir / "thread.jsonl"
    with thread_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")

    _update_context(sdir, last_active=utcnow())


def read_thread(
    repo_root: Path,
    session_id: str,
    *,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Read the conversation thread for a session.

    Returns all messages, or the last `limit` messages if specified.
    """
    thread_path = session_dir(repo_root, session_id) / "thread.jsonl"
    if not thread_path.exists():
        return []

    messages: list[dict[str, Any]] = []
    for line in thread_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if limit and len(messages) > limit:
        return messages[-limit:]
    return messages


def build_messages_for_backend(
    repo_root: Path,
    session_id: str,
    *,
    system_prompt: str,
    context_window: int = 50,
) -> list[dict[str, Any]]:
    """Build the messages list for the chat backend.

    Includes the system prompt and the last context_window thread entries,
    formatted for the OpenAI messages API.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]

    thread = read_thread(repo_root, session_id, limit=context_window)
    for entry in thread:
        msg: dict[str, Any] = {
            "role": entry["role"],
        }
        if entry.get("content"):
            msg["content"] = entry["content"]
        if entry.get("tool_calls"):
            msg["tool_calls"] = entry["tool_calls"]
        if entry.get("tool_call_id"):
            msg["tool_call_id"] = entry["tool_call_id"]
        if entry.get("name"):
            msg["name"] = entry["name"]
        # Ensure content key exists for tool messages
        if msg["role"] == "tool" and "content" not in msg:
            msg["content"] = ""
        messages.append(msg)

    return messages


def read_notes(repo_root: Path, session_id: str) -> str:
    """Read the session notes."""
    notes_path = session_dir(repo_root, session_id) / "notes.md"
    if not notes_path.exists():
        return ""
    return notes_path.read_text(encoding="utf-8")


def write_notes(repo_root: Path, session_id: str, content: str) -> None:
    """Overwrite the session notes."""
    sdir = session_dir(repo_root, session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "notes.md").write_text(content, encoding="utf-8")


def append_note(repo_root: Path, session_id: str, note: str) -> None:
    """Append a note to the session notes file."""
    sdir = session_dir(repo_root, session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    notes_path = sdir / "notes.md"
    existing = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""
    timestamp = utcnow()
    notes_path.write_text(
        f"{existing}\n- [{timestamp}] {note}\n",
        encoding="utf-8",
    )


def read_context(repo_root: Path, session_id: str) -> dict[str, Any]:
    """Read session metadata."""
    ctx_path = session_dir(repo_root, session_id) / "context.json"
    if not ctx_path.exists():
        return {}
    try:
        return json.loads(ctx_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def list_sessions(repo_root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    """List recent sessions with their context metadata."""
    root = sessions_root(repo_root)
    if not root.exists():
        return []

    sessions: list[dict[str, Any]] = []
    for sdir in sorted(root.iterdir(), reverse=True):
        if not sdir.is_dir():
            continue
        ctx = read_context(repo_root, sdir.name)
        ctx["session_id"] = sdir.name
        thread_path = sdir / "thread.jsonl"
        ctx["message_count"] = (
            sum(1 for line in thread_path.read_text(encoding="utf-8").splitlines() if line.strip())
            if thread_path.exists()
            else 0
        )
        sessions.append(ctx)
        if len(sessions) >= limit:
            break

    return sessions


def _update_context(sdir: Path, **updates: Any) -> None:
    """Update fields in context.json."""
    ctx_path = sdir / "context.json"
    ctx: dict[str, Any] = {}
    if ctx_path.exists():
        try:
            ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            ctx = {}
    ctx.update(updates)
    ctx_path.write_text(
        json.dumps(ctx, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
