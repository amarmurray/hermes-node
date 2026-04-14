from __future__ import annotations

import json
import os
import random
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .codex import codex_available, make_branch_name, run_codex
from .config import Settings
from .jsonl import append_jsonl, utcnow
from .ledger import Ledger
from .models import (
    APPROVAL_GRANTED,
    APPROVAL_NOT_REQUIRED,
    ExecutionOutcome,
    TASK_STATE_DEFERRED_RATE_LIMIT,
    TASK_STATE_DONE,
    TASK_STATE_FAILED,
    TASK_STATE_PENDING,
    TASK_STATE_PENDING_APPROVAL,
    TASK_STATE_RUNNING,
    get_task_profile,
)
from .routing import (
    EXECUTOR_CODEX,
    EXECUTOR_HERMES_CHAT,
    RISK_BRANCH_WRITE,
    get_route,
    resolve_executor,
)
from .workspaces import (
    ensure_workspace,
    write_input_artifact,
    write_run_output,
    write_run_receipt,
    write_worktree_metadata,
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
RATE_LIMIT_PATTERNS = [
    re.compile(r"HTTP 429", re.IGNORECASE),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"Provider returned error.*429", re.IGNORECASE),
]


def is_rate_limit_error(stdout: str, stderr: str) -> bool:
    combined = f"{stdout}\n{stderr}"
    return any(p.search(combined) for p in RATE_LIMIT_PATTERNS)


def compute_backoff_seconds(
    attempt_count: int,
    base: int,
    maximum: int,
    jitter_factor: float = 0.25,
) -> float:
    raw = base * (2 ** attempt_count)
    capped = min(raw, maximum)
    jitter = capped * jitter_factor * (2 * random.random() - 1)
    return max(1.0, capped + jitter)


def compute_next_attempt_at(
    attempt_count: int,
    base: int,
    maximum: int,
) -> str:
    delay = compute_backoff_seconds(attempt_count, base, maximum)
    target = datetime.now(timezone.utc) + timedelta(seconds=delay)
    return target.isoformat()


def log_wrapper_event(settings: Settings, action: str, **payload: Any) -> None:
    append_jsonl(
        settings.log_jsonl,
        {
            "timestamp": utcnow(),
            "action": action,
            **payload,
        },
    )


def available_executors(settings: Settings, task: dict[str, Any] | None = None) -> set[str]:
    """Return the set of executors that are currently available."""
    avail = {EXECUTOR_HERMES_CHAT}  # always available via upstream hermes
    requested_provider = ""
    if task is not None:
        requested_provider = str(task.get("provider", "") or "")
    if codex_available(settings, requested_provider=requested_provider):
        avail.add(EXECUTOR_CODEX)
    return avail


def ensure_task_workspace(settings: Settings, task: dict[str, Any]) -> Path:
    workspace_dir = task.get("workspace_dir", "")
    workspace_path = ensure_workspace(settings.repo_root, task["id"], workspace_dir=workspace_dir)
    write_input_artifact(
        settings.repo_root,
        task["id"],
        kind=task["kind"],
        prompt=task["prompt"],
        provider=task.get("provider", ""),
        model=task.get("model", ""),
        repo_path=task.get("repo_path", ""),
        metadata=task.get("metadata", {}),
        created_at=task.get("created_at", ""),
        workspace_dir=workspace_dir,
    )
    if task.get("repo_path"):
        profile = get_task_profile(task["kind"])
        write_worktree_metadata(
            settings.repo_root,
            task["id"],
            repo_path=task["repo_path"],
            worktree_path=task["repo_path"],
            extra={"uses_upstream_worktree": profile.use_worktree},
            workspace_dir=workspace_dir,
        )
    return workspace_path


def write_run_artifacts(
    settings: Settings,
    task: dict[str, Any],
    run: dict[str, Any],
    *,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    duration_ms: int | None,
    executor: str,
    provider: str,
    model: str,
    task_state: str,
    hermes_session_id: str = "",
    branch: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    workspace_dir = task.get("workspace_dir", "")
    write_run_output(
        settings.repo_root,
        task["id"],
        run["id"],
        stdout=stdout,
        stderr=stderr,
        workspace_dir=workspace_dir,
    )
    receipt_extra = {
        "run_state": run.get("state", ""),
        "task_state": task_state,
    }
    if extra:
        receipt_extra.update(extra)
    write_run_receipt(
        settings.repo_root,
        task["id"],
        run["id"],
        exit_code=exit_code,
        duration_ms=duration_ms,
        executor=executor,
        provider=provider,
        model=model,
        state=task_state,
        created_at=run.get("created_at", ""),
        started_at=run.get("started_at", ""),
        finished_at=run.get("finished_at", ""),
        working_dir=run.get("working_dir", ""),
        command=run.get("command", []),
        hermes_session_id=hermes_session_id,
        branch=branch,
        extra=receipt_extra,
        workspace_dir=workspace_dir,
    )


# -- Hermes chat executor --

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


# -- Executor dispatch --

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
    if task["state"] not in {TASK_STATE_PENDING, TASK_STATE_PENDING_APPROVAL, TASK_STATE_DEFERRED_RATE_LIMIT}:
        raise ValueError(f"Task {task_id} is not runnable from state {task['state']}")

    ensure_task_workspace(settings, task)

    # Resolve executor via routing policy
    route = get_route(task["kind"])
    avail = available_executors(settings, task=task)
    executor = resolve_executor(task["kind"], available_executors=avail)

    if executor is None:
        # No executor available — fail legibly
        summary = (
            f"No executor available for kind={task['kind']}. "
            f"Route requires {route.default_executor}, "
            f"fallbacks={list(route.fallback_executors)}, "
            f"available={sorted(avail)}."
        )
        log_wrapper_event(
            settings, "task_run.no_executor",
            task_id=task_id, kind=task["kind"],
            required=route.default_executor, available=sorted(avail),
        )
        ledger._conn.execute(
            "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
            (TASK_STATE_FAILED, utcnow(), task_id),
        )
        ledger._conn.commit()
        ledger.add_event(
            task_id, event_type="task.no_executor", level="error",
            message=summary,
        )
        return ExecutionOutcome(
            task_id=task_id, run_id=None, state=TASK_STATE_FAILED,
            exit_code=None, summary=summary, executor=route.default_executor,
        )

    if executor == EXECUTOR_CODEX:
        return _execute_via_codex(settings, ledger, task, route)
    else:
        return _execute_via_hermes(settings, ledger, task, executor, command_runner=command_runner)


def _execute_via_hermes(
    settings: Settings,
    ledger: Ledger,
    task: dict[str, Any],
    executor: str,
    *,
    command_runner=run_command,
) -> ExecutionOutcome:
    """Run a task through the upstream Hermes chat executor."""
    task_id = task["id"]
    command, provider, model, max_turns, workdir = build_task_command(settings, task=task)
    uses_upstream_worktree = "--worktree" in command
    task, run = ledger.begin_run(
        task_id,
        command=command,
        working_dir=workdir,
        provider=provider,
        model=model,
        max_turns=max_turns,
        executor=executor,
    )
    log_wrapper_event(
        settings,
        "task_run.started",
        task_id=task_id,
        run_id=run["id"],
        kind=task["kind"],
        executor=executor,
        provider=provider,
        model=model,
        working_dir=workdir,
    )
    write_run_artifacts(
        settings,
        task,
        run,
        stdout="",
        stderr="",
        exit_code=None,
        duration_ms=None,
        executor=executor,
        provider=provider,
        model=model,
        task_state=TASK_STATE_RUNNING,
        extra={"uses_upstream_worktree": uses_upstream_worktree},
    )
    if task["repo_path"]:
        write_worktree_metadata(
            settings.repo_root,
            task_id,
            repo_path=task["repo_path"],
            worktree_path=workdir,
            extra={"uses_upstream_worktree": uses_upstream_worktree},
            workspace_dir=task.get("workspace_dir", ""),
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

    # Detect rate-limit and defer instead of hard-failing
    if result.exit_code != 0 and is_rate_limit_error(result.stdout, result.stderr):
        return _handle_rate_limit(
            settings, ledger, task, run, result, session_id,
            executor=executor, provider=provider, model=model,
        )

    # Normal completion or non-rate-limit failure
    task, run = ledger.finish_run(
        run["id"],
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        hermes_session_id=session_id,
    )
    write_run_artifacts(
        settings,
        task,
        run,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        executor=executor,
        provider=provider,
        model=model,
        task_state=task["state"],
        hermes_session_id=session_id,
        extra={"uses_upstream_worktree": uses_upstream_worktree},
    )
    summary = summarize_command_result(task, run)
    ledger.add_summary(
        task_id,
        run_id=run["id"],
        content=summary,
        metadata={"exit_code": result.exit_code, "hermes_session_id": session_id, "executor": executor},
    )
    log_wrapper_event(
        settings,
        "task_run.finished",
        task_id=task_id,
        run_id=run["id"],
        state=task["state"],
        executor=executor,
        exit_code=result.exit_code,
        hermes_session_id=session_id,
    )
    return ExecutionOutcome(
        task_id=task_id,
        run_id=run["id"],
        state=task["state"],
        exit_code=result.exit_code,
        summary=summary,
        executor=executor,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _execute_via_codex(
    settings: Settings,
    ledger: Ledger,
    task: dict[str, Any],
    route,
) -> ExecutionOutcome:
    """Run a task through the Codex executor."""
    task_id = task["id"]
    repo_path = task["repo_path"]
    if not repo_path:
        raise ValueError(f"Task {task_id} requires a repo_path for Codex execution")

    writable = route.risk_class == RISK_BRANCH_WRITE
    branch = make_branch_name(task_id) if writable else ""
    provider = task["provider"] or settings.default_provider or "codex"
    model = task["model"] or settings.default_model or "codex"

    task_obj, run = ledger.begin_run(
        task_id,
        command=[],
        working_dir=repo_path,
        provider=provider,
        model=model,
        max_turns=0,
        executor=EXECUTOR_CODEX,
    )
    log_wrapper_event(
        settings,
        "task_run.started",
        task_id=task_id,
        run_id=run["id"],
        kind=task["kind"],
        executor=EXECUTOR_CODEX,
        writable=writable,
        branch=branch,
        repo_path=repo_path,
    )
    write_run_artifacts(
        settings,
        task_obj,
        run,
        stdout="",
        stderr="",
        exit_code=None,
        duration_ms=None,
        executor=EXECUTOR_CODEX,
        provider=provider,
        model=model,
        task_state=TASK_STATE_RUNNING,
        branch=branch,
    )
    write_worktree_metadata(
        settings.repo_root,
        task_id,
        repo_path=repo_path,
        branch=branch,
        worktree_path=repo_path,
        extra={"executor": EXECUTOR_CODEX, "writable": writable},
        workspace_dir=task_obj.get("workspace_dir", ""),
    )

    try:
        codex_result = run_codex(
            settings,
            prompt=task["prompt"],
            repo_path=repo_path,
            provider=provider,
            model=model,
            task_id=task_id,
            run_id=run["id"],
            writable=writable,
            branch=branch,
        )
        ledger._conn.execute(
            "UPDATE runs SET command = ? WHERE id = ?",
            (json.dumps(codex_result.command), run["id"]),
        )
        ledger._conn.commit()
        run = ledger.get_run(run["id"])
    except Exception as exc:
        # Codex call itself failed (e.g. missing API key, binary crash)
        error_msg = f"Codex executor error: {exc}"
        task_obj, run = ledger.finish_run(
            run["id"],
            exit_code=127,
            stdout="",
            stderr=error_msg,
            duration_ms=0,
        )
        write_run_artifacts(
            settings,
            task_obj,
            run,
            stdout="",
            stderr=error_msg,
            exit_code=127,
            duration_ms=0,
            executor=EXECUTOR_CODEX,
            provider=provider,
            model=model,
            task_state=task_obj["state"],
            branch=branch,
        )
        ledger.add_event(
            task_id, run_id=run["id"],
            event_type="run.codex_error", level="error",
            message=error_msg,
        )
        log_wrapper_event(settings, "task_run.codex_error", task_id=task_id, run_id=run["id"], error=str(exc))
        return ExecutionOutcome(
            task_id=task_id, run_id=run["id"], state=TASK_STATE_FAILED,
            exit_code=127, summary=error_msg, executor=EXECUTOR_CODEX,
        )

    # Rate-limit check for Codex too
    if codex_result.exit_code != 0 and is_rate_limit_error(codex_result.stdout, codex_result.stderr):
        cr = CommandResult(
            command=codex_result.command,
            exit_code=codex_result.exit_code,
            stdout=codex_result.stdout,
            stderr=codex_result.stderr,
            duration_ms=codex_result.duration_ms,
        )
        return _handle_rate_limit(
            settings, ledger, task_obj, run, cr, "",
            executor=EXECUTOR_CODEX, provider=provider, model=model, branch=branch,
        )

    task_obj, run = ledger.finish_run(
        run["id"],
        exit_code=codex_result.exit_code,
        stdout=codex_result.stdout,
        stderr=codex_result.stderr,
        duration_ms=codex_result.duration_ms,
    )
    write_run_artifacts(
        settings,
        task_obj,
        run,
        stdout=codex_result.stdout,
        stderr=codex_result.stderr,
        exit_code=codex_result.exit_code,
        duration_ms=codex_result.duration_ms,
        executor=EXECUTOR_CODEX,
        provider=provider,
        model=model,
        task_state=task_obj["state"],
        branch=branch,
        extra={"auth_mode": codex_result.auth_mode, "provider_id": codex_result.provider_id},
    )
    summary = summarize_command_result(task_obj, run)
    metadata: dict[str, Any] = {
        "exit_code": codex_result.exit_code,
        "executor": EXECUTOR_CODEX,
        "auth_mode": codex_result.auth_mode,
        "provider_id": codex_result.provider_id,
    }
    if branch:
        metadata["branch"] = branch
    ledger.add_summary(task_id, run_id=run["id"], content=summary, metadata=metadata)
    log_wrapper_event(
        settings,
        "task_run.finished",
        task_id=task_id,
        run_id=run["id"],
        state=task_obj["state"],
        executor=EXECUTOR_CODEX,
        exit_code=codex_result.exit_code,
        branch=branch,
    )
    return ExecutionOutcome(
        task_id=task_id,
        run_id=run["id"],
        state=task_obj["state"],
        exit_code=codex_result.exit_code,
        summary=summary,
        executor=EXECUTOR_CODEX,
        stdout=codex_result.stdout,
        stderr=codex_result.stderr,
    )


# -- Rate-limit handling (shared) --

def _handle_rate_limit(
    settings: Settings,
    ledger: Ledger,
    task: dict[str, Any],
    run: dict[str, Any],
    result: CommandResult,
    session_id: str,
    *,
    executor: str,
    provider: str,
    model: str,
    branch: str = "",
) -> ExecutionOutcome:
    task_id = task["id"]
    attempt_count = task.get("attempt_count", 0)
    max_attempts = task.get("max_attempts", settings.max_attempts)

    _, finished_run = ledger.finish_run(
        run["id"],
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        hermes_session_id=session_id,
    )

    if attempt_count + 1 >= max_attempts:
        ledger._conn.execute(
            "UPDATE tasks SET state = ?, updated_at = ?, attempt_count = attempt_count + 1 WHERE id = ?",
            (TASK_STATE_FAILED, utcnow(), task_id),
        )
        ledger._conn.commit()
        task_after = ledger.get_task(task_id)
        ledger.add_event(
            task_id, run_id=run["id"],
            event_type="task.max_attempts_exceeded", level="error",
            message=f"Task exhausted {max_attempts} attempts due to rate limiting",
        )
        log_wrapper_event(
            settings, "task_run.max_attempts_exceeded",
            task_id=task_id, run_id=run["id"], executor=executor,
            attempt_count=attempt_count + 1, max_attempts=max_attempts,
        )
        summary = f"Task {task_id} permanently failed after {max_attempts} rate-limit retries."
        ledger.add_summary(task_id, run_id=run["id"], content=summary, metadata={"rate_limited": True})
        write_run_artifacts(
            settings,
            task_after,
            finished_run,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            executor=executor,
            provider=provider,
            model=model,
            task_state=task_after["state"],
            hermes_session_id=session_id,
            branch=branch,
        )
        return ExecutionOutcome(
            task_id=task_id, run_id=run["id"], state=TASK_STATE_FAILED,
            exit_code=result.exit_code, summary=summary, executor=executor,
            stdout=result.stdout, stderr=result.stderr,
        )

    next_at = compute_next_attempt_at(
        attempt_count, settings.base_backoff_seconds, settings.max_backoff_seconds,
    )
    reason = f"Rate limited (attempt {attempt_count + 1}/{max_attempts}). Next retry after {next_at}."
    task_after = ledger.defer_task_rate_limit(task_id, run_id=run["id"], next_attempt_at=next_at, reason=reason)
    ledger.set_provider_cooldown(provider, model, cooldown_until=next_at, last_error=result.stderr[:500])
    write_run_artifacts(
        settings,
        task_after,
        finished_run,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        executor=executor,
        provider=provider,
        model=model,
        task_state=task_after["state"],
        hermes_session_id=session_id,
        branch=branch,
    )
    log_wrapper_event(
        settings, "task_run.deferred_rate_limit",
        task_id=task_id, run_id=run["id"], executor=executor,
        attempt_count=attempt_count + 1, next_attempt_at=next_at,
        provider=provider, model=model,
    )
    return ExecutionOutcome(
        task_id=task_id, run_id=run["id"], state=TASK_STATE_DEFERRED_RATE_LIMIT,
        exit_code=result.exit_code, summary=reason, executor=executor,
        stdout=result.stdout, stderr=result.stderr,
    )


# -- Summary helpers --

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
        "deferred_tasks": ledger.deferred_tasks(limit=limit),
        "active_cooldowns": ledger.active_cooldowns(),
        "recent_rate_limit_events": ledger.recent_rate_limit_events(limit=limit),
    }


def extract_session_id(stdout: str) -> str:
    match = SESSION_ID_RE.search(stdout)
    return match.group(1) if match else ""


def extract_response_text(stdout: str) -> str:
    text = ANSI_RE.sub("", stdout).replace("\r", "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    codex_indices = [idx for idx, line in enumerate(lines) if line == "codex"]
    if codex_indices:
        assistant_lines: list[str] = []
        for line in lines[codex_indices[-1] + 1:]:
            if line == "tokens used":
                break
            assistant_lines.append(line)
        if assistant_lines:
            return "\n".join(assistant_lines).strip()
    return clean_console_text(stdout)


def clean_console_text(text: str) -> str:
    text = ANSI_RE.sub("", text).replace("\r", "")
    cleaned: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "--------":
            continue
        if line in {"user", "codex"}:
            continue
        if line.startswith("Reading additional input from stdin"):
            continue
        if line.startswith("OpenAI Codex v"):
            continue
        if line.startswith("workdir:"):
            continue
        if line.startswith("model:"):
            continue
        if line.startswith("provider:"):
            continue
        if line.startswith("approval:"):
            continue
        if line.startswith("sandbox:"):
            continue
        if line.startswith("reasoning effort:"):
            continue
        if line.startswith("reasoning summaries:"):
            continue
        if line.startswith("session id:"):
            continue
        if line.startswith("warning: Codex could not find bubblewrap"):
            continue
        if line == "tokens used":
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
