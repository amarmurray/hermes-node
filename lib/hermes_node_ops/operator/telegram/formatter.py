"""Mobile-friendly rendering helpers for Telegram responses.

Formats operator outputs as compact plain text (no Markdown parse_mode,
to avoid escaping headaches). Optimized for small screens.
"""

from __future__ import annotations

import json
from typing import Any


def format_queue_status(payload: dict[str, Any]) -> str:
    """Format a get_queue_status payload compactly."""
    lines: list[str] = []
    counts = payload.get("counts", {}) or {}
    if counts:
        count_str = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        lines.append(f"queue: {count_str}")
    else:
        lines.append("queue: empty")

    deferred = payload.get("deferred_tasks", []) or []
    if deferred:
        lines.append(f"deferred: {len(deferred)}")
        for t in deferred[:3]:
            lines.append(f"  {_short(t.get('id',''))} next={t.get('next_attempt_at','?')}")

    cooldowns = payload.get("active_cooldowns", []) or []
    if cooldowns:
        lines.append(f"cooldowns: {len(cooldowns)}")

    recent_tasks = payload.get("recent_tasks", []) or []
    if recent_tasks:
        lines.append("")
        lines.append("recent tasks:")
        for t in recent_tasks[:5]:
            title = t.get("title") or t.get("kind", "?")
            lines.append(f"  {_short(t.get('id',''))} [{t.get('state','?')}] {title}")

    recent_runs = payload.get("recent_runs", []) or []
    if recent_runs:
        lines.append("")
        lines.append("recent runs:")
        for r in recent_runs[:5]:
            exit_code = r.get("exit_code")
            marker = "ok" if exit_code == 0 else f"exit={exit_code}"
            lines.append(
                f"  {_short(r.get('id',''))} [{r.get('state','?')}] "
                f"{r.get('executor','?')} {marker}"
            )

    return "\n".join(lines)


def format_task_status(payload: dict[str, Any]) -> str:
    """Format a get_task_status payload compactly."""
    task = payload.get("task", {}) or {}
    lines = [
        f"task: {task.get('id','?')}",
        f"kind: {task.get('kind','?')}",
        f"state: {task.get('state','?')}",
    ]
    if task.get("approval_state"):
        lines.append(f"approval: {task['approval_state']}")
    if task.get("attempt_count"):
        lines.append(f"attempts: {task['attempt_count']}")
    if task.get("prompt_preview"):
        lines.append("")
        lines.append(f"prompt: {task['prompt_preview']}")

    latest_summary = payload.get("latest_summary") or {}
    if latest_summary.get("content"):
        lines.append("")
        lines.append("summary:")
        lines.append(latest_summary["content"])

    latest_run = payload.get("latest_run") or {}
    if latest_run:
        lines.append("")
        lines.append(
            f"latest run: {_short(latest_run.get('id',''))} "
            f"[{latest_run.get('state','?')}] exit={latest_run.get('exit_code','?')}"
        )

    return "\n".join(lines)


def format_task_list(tasks: list[dict[str, Any]], *, header: str = "tasks") -> str:
    """Format a list of tasks compactly."""
    if not tasks:
        return f"no {header}"
    lines = [f"{header}:"]
    for t in tasks[:15]:
        title = t.get("title") or t.get("kind", "?")
        lines.append(f"  {_short(t.get('id',''))} [{t.get('state','?')}] {title}")
    return "\n".join(lines)


def format_pending_approvals(tasks: list[dict[str, Any]]) -> str:
    """Format pending-approval tasks with action hints."""
    pending = [t for t in tasks if t.get("state") == "pending_approval"]
    if not pending:
        return "no tasks awaiting approval"
    lines = [f"pending approval ({len(pending)}):"]
    for t in pending[:10]:
        title = t.get("title") or t.get("kind", "?")
        lines.append(f"  {t.get('id','?')} {title}")
    lines.append("")
    lines.append("use /approve <task_id> to approve")
    return "\n".join(lines)


def format_run_list(runs: list[dict[str, Any]]) -> str:
    """Format a list of runs compactly."""
    if not runs:
        return "no runs"
    lines = ["recent runs:"]
    for r in runs[:10]:
        exit_code = r.get("exit_code")
        marker = "ok" if exit_code == 0 else f"exit={exit_code}"
        lines.append(
            f"  {_short(r.get('id',''))} [{r.get('state','?')}] "
            f"{r.get('executor','?')} {marker}"
        )
    return "\n".join(lines)


def format_tool_result_compact(name: str, result: str) -> str:
    """Format a tool result for inline display in the conversation."""
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return f"[{name}] {result[:200]}"

    if isinstance(data, dict) and "error" in data:
        return f"[{name}] error: {data['error']}"

    if name == "get_queue_status":
        return format_queue_status(data)
    if name == "get_task_status":
        return format_task_status(data)
    if name in ("list_recent_tasks",):
        return format_task_list(data, header="recent tasks")
    if name == "list_recent_runs":
        return format_run_list(data if isinstance(data, list) else [])
    if name == "submit_task" and isinstance(data, dict):
        return f"submitted {data.get('task_id','?')} [{data.get('state','?')}]"
    if name == "approve_task" and isinstance(data, dict):
        if data.get("approved"):
            return f"approved {data.get('task_id','?')} -> {data.get('new_state','?')}"
        return f"not approved: {data.get('error','?')}"

    # Generic fallback: compact JSON
    return f"[{name}] " + json.dumps(data, separators=(",", ":"))[:600]


def format_assistant_response(content: str) -> str:
    """Sanitize and cap an assistant response for Telegram."""
    content = content.strip()
    # Strip common thinking tags some small models emit
    for tag in ("<think>", "</think>", "<thinking>", "</thinking>"):
        content = content.replace(tag, "")
    content = content.strip()
    if len(content) > 3500:
        content = content[:3500] + "..."
    return content


def format_alert(event_type: str, payload: dict[str, Any]) -> str:
    """Format an alert message."""
    task_id = payload.get("task_id", "?")
    msg = payload.get("message", "")
    if event_type == "task.failed":
        return f"FAILED: {task_id}\n{msg}"
    if event_type == "task.deferred_rate_limit":
        return f"DEFERRED (rate-limit): {task_id}\n{msg}"
    if event_type == "task.pending_approval":
        return f"NEEDS APPROVAL: {task_id}\nuse /approve {task_id}"
    return f"{event_type}: {task_id}\n{msg}"


def _short(ident: str) -> str:
    """Abbreviate a long hermes-node ID for mobile display."""
    if not ident:
        return "?"
    if len(ident) <= 30:
        return ident
    # Keep prefix + last 10 chars
    return f"{ident[:15]}..{ident[-10:]}"
