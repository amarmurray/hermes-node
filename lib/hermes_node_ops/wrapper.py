from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .jsonl import append_jsonl, utcnow
from .ledger import Ledger
from .models import (
    APPROVAL_GRANTED,
    APPROVAL_NOT_REQUIRED,
    ExecutionOutcome,
    TASK_STATE_PENDING,
    TASK_STATE_WAITING_APPROVAL,
    get_task_profile,
)


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
SESSION_ID_RE = re.compile(r"session_id:\s*(\S+)")


def log_wrapper_event(settings: Settings, action: str, **payload: Any) -> None:
    append_jsonl(
        settings.log_jsonl,
        {
            "timestamp": utcnow(),
            "action": action,
            **payload,
        },
    )


def build_upstream_command(settings: Settings, *args: str) -> list[str]:
    return [str(settings.resolved_hermes_bin), *args]


def build_task_command(
    settings: Settings,
    *,
    task: dict[str, Any],
) -> tuple[list[str], str, str, int, str]:
    profile = get_task_profile(task["kind"])
    provider = task["provider"] or settings.default_provider
    model = task["model"] or settings.default_model
    max_turns = int(task["max_turns"] or settings.default_max_turns)
    if not provider:
        raise ValueError("No provider configured for task")
    if not model:
        raise ValueError("No model configured for task")

    prompt_parts = [profile.prompt_preamble]
    if task["repo_path"]:
        prompt_parts.append(f"Repository root: {task['repo_path']}")
    prompt_parts.append(task["prompt"])
    prompt = "\n\n".join(part for part in prompt_parts if part)

    command = build_upstream_command(
        settings,
        "chat",
        "-q",
        prompt,
        "-Q",
        "--provider",
        provider,
        "--model",
        model,
        "--max-turns",
        str(max_turns),
        "--source",
        settings.source_tag,
        "-t",
        ",".join(task["toolsets"]),
    )
    workdir = task["repo_path"] or str(settings.repo_root)
    if profile.use_worktree and task["repo_path"] and _repo_looks_like_git_checkout(Path(task["repo_path"])):
        command.append("--worktree")
    return command, provider, model, max_turns, workdir


def run_command(
    settings: Settings,
    command: list[str],
    *,
    action: str,
    capture_output: bool = True,
    cwd: str | None = None,
    task_id: str = "",
    run_id: str = "",
) -> CommandResult:
    env = dict(**os.environ, HERMES_HOME=str(settings.upstream_home))
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        capture_output=capture_output,
        text=True,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        env=env,
        check=False,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    stdout = completed.stdout if capture_output else ""
    stderr = completed.stderr if capture_output else ""
    append_jsonl(
        settings.log_jsonl,
        {
            "timestamp": utcnow(),
            "action": action,
            "command": command,
            "cwd": cwd or "",
            "task_id": task_id,
            "run_id": run_id,
            "exit_code": completed.returncode,
            "duration_ms": duration_ms,
            "stdout_preview": stdout[:400],
            "stderr_preview": stderr[:400],
        },
    )
    return CommandResult(
        command=command,
        exit_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
    )


def execute_task(
    settings: Settings,
    ledger: Ledger,
    task_id: str,
    *,
    command_runner=run_command,
) -> ExecutionOutcome:
    task = ledger.get_task(task_id)
    if task["approval_state"] not in {APPROVAL_NOT_REQUIRED, APPROVAL_GRANTED}:
        raise ValueError(f"Task {task_id} is awaiting approval")
    if task["state"] not in {TASK_STATE_PENDING, TASK_STATE_WAITING_APPROVAL}:
        raise ValueError(f"Task {task_id} is not runnable from state {task['state']}")

    command, provider, model, max_turns, workdir = build_task_command(settings, task=task)
    task, run = ledger.begin_run(
        task_id,
        command=command,
        working_dir=workdir,
        provider=provider,
        model=model,
        max_turns=max_turns,
    )
    log_wrapper_event(
        settings,
        "task_run.started",
        task_id=task_id,
        run_id=run["id"],
        kind=task["kind"],
        provider=provider,
        model=model,
        working_dir=workdir,
    )
    result = command_runner(
        settings,
        command,
        action="task_run",
        capture_output=True,
        cwd=workdir,
        task_id=task_id,
        run_id=run["id"],
    )
    session_id = extract_session_id(result.stdout)
    task, run = ledger.finish_run(
        run["id"],
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        hermes_session_id=session_id,
    )
    summary = summarize_command_result(task, run)
    ledger.add_summary(
        task_id,
        run_id=run["id"],
        content=summary,
        metadata={"exit_code": result.exit_code, "hermes_session_id": session_id},
    )
    log_wrapper_event(
        settings,
        "task_run.finished",
        task_id=task_id,
        run_id=run["id"],
        state=task["state"],
        exit_code=result.exit_code,
        hermes_session_id=session_id,
    )
    return ExecutionOutcome(
        task_id=task_id,
        run_id=run["id"],
        state=task["state"],
        exit_code=result.exit_code,
        summary=summary,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def summarize_command_result(task: dict[str, Any], run: dict[str, Any]) -> str:
    response = extract_response_text(run["stdout"])
    if response:
        return response[:2000]
    stderr = clean_console_text(run["stderr"])
    if stderr:
        return f"Task {task['id']} failed with exit code {run['exit_code']}: {stderr[:1600]}"
    return f"Task {task['id']} finished with state {run['state']} and exit code {run['exit_code']}."


def ensure_summary(ledger: Ledger, task_id: str = "", run_id: str = "") -> dict[str, Any]:
    existing = ledger.latest_summary(task_id=task_id, run_id=run_id)
    if existing:
        return existing
    if run_id:
        run = ledger.get_run(run_id)
        task = ledger.get_task(run["task_id"])
    elif task_id:
        task = ledger.get_task(task_id)
        run = ledger.latest_run_for_task(task_id)
        if run is None:
            raise ValueError(f"Task {task_id} has no runs to summarize")
    else:
        existing = ledger.latest_summary()
        if existing:
            return existing
        raise ValueError("No summaries recorded yet")
    content = summarize_command_result(task, run)
    return ledger.add_summary(task["id"], run_id=run["id"], content=content)


def format_status_snapshot(ledger: Ledger, *, limit: int = 5) -> dict[str, Any]:
    return {
        "counts": ledger.task_counts(),
        "recent_tasks": ledger.recent_tasks(limit=limit),
        "recent_runs": ledger.recent_runs(limit=limit),
    }


def extract_session_id(stdout: str) -> str:
    match = SESSION_ID_RE.search(stdout)
    return match.group(1) if match else ""


def extract_response_text(stdout: str) -> str:
    return clean_console_text(stdout)


def clean_console_text(text: str) -> str:
    text = ANSI_RE.sub("", text).replace("\r", "")
    cleaned: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("session_id:"):
            continue
        if "Reached maximum iterations" in line:
            continue
        if "⚕ Hermes" in line:
            continue
        if set(line) <= {"─", "╭", "╮", "╰", "╯", "│", "┊"}:
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _repo_looks_like_git_checkout(path: Path) -> bool:
    return (path / ".git").exists()
