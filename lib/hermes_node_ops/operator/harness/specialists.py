"""Bounded specialist subagents for the operator harness.

These are not a second control plane. They are small, file-backed,
policy-constrained helpers that produce durable artifacts for the
parent operator session to inspect.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ...config import Settings
from ...ledger import Ledger
from ...wrapper import format_status_snapshot


SPECIALIST_ROLES = (
    "rate-limit-triage",
    "repo-diagnostician",
)


def run_specialist(
    settings: Settings,
    *,
    role: str,
    goal: str,
    context: str = "",
    repo_path: str = "",
) -> str:
    """Execute a bounded specialist and return its markdown result."""
    if role == "rate-limit-triage":
        return _run_rate_limit_triage(settings, goal=goal, context=context)
    if role == "repo-diagnostician":
        return _run_repo_diagnostician(settings, goal=goal, context=context, repo_path=repo_path)
    raise ValueError(f"Unsupported specialist role: {role}")


def _run_rate_limit_triage(
    settings: Settings,
    *,
    goal: str,
    context: str,
) -> str:
    with Ledger(settings.ledger_db) as ledger:
        snapshot = format_status_snapshot(ledger, limit=10)

    deferred = snapshot.get("deferred_tasks", []) or []
    cooldowns = snapshot.get("active_cooldowns", []) or []
    events = snapshot.get("recent_rate_limit_events", []) or []

    lines = [
        "# Rate-Limit Triage",
        "",
        f"Goal: {goal}",
    ]
    if context.strip():
        lines.extend(["", "Context:", context.strip()])

    lines.extend(
        [
            "",
            "## Current State",
            f"- Active cooldowns: {len(cooldowns)}",
            f"- Deferred tasks: {len(deferred)}",
            f"- Recent rate-limit events: {len(events)}",
        ]
    )

    if deferred:
        lines.append("")
        lines.append("## Deferred Tasks")
        for task in deferred[:5]:
            lines.append(
                f"- {task.get('id', '?')} [{task.get('kind', '?')}] "
                f"next_attempt_at={task.get('next_attempt_at', '?')}"
            )

    if cooldowns:
        lines.append("")
        lines.append("## Active Cooldowns")
        for cooldown in cooldowns[:5]:
            key = cooldown.get("cooldown_key", "?")
            until = cooldown.get("cooldown_until", "?")
            reason = cooldown.get("reason", "")
            suffix = f" reason={reason}" if reason else ""
            lines.append(f"- {key} until {until}{suffix}")

    if events:
        lines.append("")
        lines.append("## Recent Signals")
        for event in events[:5]:
            lines.append(
                f"- {event.get('created_at', '?')}: "
                f"{event.get('message', '').strip()}"
            )

    lines.extend(["", "## Recommendation"])
    if cooldowns or deferred or events:
        lines.extend(
            [
                "- Keep retries inside hermes-node backoff boundaries.",
                "- Prefer waiting for cooldown expiry over ad hoc replays.",
                "- If this persists, narrow provider/model usage through routing rather than bypassing policy.",
            ]
        )
    else:
        lines.append("- No active rate-limit pressure is visible in the ledger right now.")

    return "\n".join(lines).strip() + "\n"


def _run_repo_diagnostician(
    settings: Settings,
    *,
    goal: str,
    context: str,
    repo_path: str,
) -> str:
    target = Path(repo_path or settings.repo_root).expanduser().resolve()
    if not target.exists():
        raise ValueError(f"Repository path does not exist: {repo_path or settings.repo_root}")
    if target.is_file():
        target = target.parent

    lines = [
        "# Repo Diagnostician",
        "",
        f"Goal: {goal}",
        f"Repository: {target}",
    ]
    if context.strip():
        lines.extend(["", "Context:", context.strip()])

    if not (target / ".git").exists():
        lines.extend(
            [
                "",
                "## State",
                "- This path is not a git checkout.",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    branch = _run_git(target, ["rev-parse", "--abbrev-ref", "HEAD"])
    status = _run_git(target, ["status", "--porcelain"])
    recent = _run_git(target, ["log", "--oneline", "-5"])

    lines.extend(
        [
            "",
            "## Git State",
            f"- Branch: {branch[0] if branch else '?'}",
            f"- Dirty: {'yes' if status else 'no'}",
        ]
    )

    if status:
        lines.append("")
        lines.append("## Changes")
        for line in status[:10]:
            lines.append(f"- {line}")

    if recent:
        lines.append("")
        lines.append("## Recent Commits")
        for line in recent[:5]:
            lines.append(f"- {line}")

    try:
        entries = sorted(
            entry.name for entry in target.iterdir()
            if entry.name != ".git"
        )[:12]
    except OSError:
        entries = []

    if entries:
        lines.append("")
        lines.append("## Top-Level Entries")
        for entry in entries:
            lines.append(f"- {entry}")

    lines.extend(
        [
            "",
            "## Recommendation",
            "- Keep any mutation requests routed through hermes-node task submission and approvals.",
        ]
    )

    return "\n".join(lines).strip() + "\n"


def _run_git(repo_path: Path, args: list[str]) -> list[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line for line in result.stdout.strip().splitlines() if line.strip()]
