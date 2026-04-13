from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .jsonl import append_jsonl, utcnow


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


def build_upstream_command(settings: Settings, *args: str) -> list[str]:
    return [str(settings.resolved_hermes_bin), *args]


def build_service_command(settings: Settings, action: str, system: bool | None = None) -> list[str]:
    scope = settings.systemd_scope if system is None else ("system" if system else "user")
    base = build_upstream_command(settings, "gateway", action)
    if scope == "system":
        command = ["sudo", *base, "--system"]
        if action == "install" and settings.service_user:
            command.extend(["--run-as-user", settings.service_user])
        return command
    return base


def build_submit_command(
    settings: Settings,
    *,
    query: str,
    provider: str = "",
    model: str = "",
    quiet: bool = True,
    max_turns: int | None = None,
    worktree: bool = False,
    yolo: bool = False,
) -> list[str]:
    command = build_upstream_command(settings, "chat", "-q", query)
    if quiet:
        command.append("-Q")
    effective_provider = provider or settings.default_provider
    effective_model = model or settings.default_model
    if effective_provider:
        command.extend(["--provider", effective_provider])
    if effective_model:
        command.extend(["--model", effective_model])
    if max_turns is not None:
        command.extend(["--max-turns", str(max_turns)])
    if worktree:
        command.append("--worktree")
    if yolo:
        command.append("--yolo")
    return command


def build_api_submit_request(
    settings: Settings,
    *,
    input_text: str,
    instructions: str = "",
    conversation: str = "",
    store: bool = True,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    body: dict[str, Any] = {
        "model": "hermes-agent",
        "input": input_text,
        "store": store,
    }
    if instructions:
        body["instructions"] = instructions
    if conversation:
        body["conversation"] = conversation
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    return f"{settings.api_base_url.rstrip('/')}/responses", headers, body


def run_command(
    settings: Settings,
    command: list[str],
    *,
    action: str,
    capture_output: bool = True,
) -> CommandResult:
    env = dict(**os.environ, HERMES_HOME=str(settings.upstream_home))
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        capture_output=capture_output,
        text=True,
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


def api_health(settings: Settings) -> tuple[int, str]:
    health_base = settings.api_base_url.rstrip("/")
    if health_base.endswith("/v1"):
        health_base = health_base.removesuffix("/v1")
    request = urllib.request.Request(f"{health_base}/health", method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    duration_ms = int((time.perf_counter() - started) * 1000)
    append_jsonl(
        settings.log_jsonl,
        {
            "timestamp": utcnow(),
            "action": "api_health",
            "status": status,
            "duration_ms": duration_ms,
            "payload_preview": payload[:400],
        },
    )
    return status, payload


def api_submit(
    settings: Settings,
    *,
    input_text: str,
    instructions: str = "",
    conversation: str = "",
    store: bool = True,
) -> tuple[int, str]:
    url, headers, body = build_api_submit_request(
        settings,
        input_text=input_text,
        instructions=instructions,
        conversation=conversation,
        store=store,
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    duration_ms = int((time.perf_counter() - started) * 1000)
    append_jsonl(
        settings.log_jsonl,
        {
            "timestamp": utcnow(),
            "action": "api_submit",
            "status": status,
            "duration_ms": duration_ms,
            "request": body,
            "payload_preview": payload[:400],
        },
    )
    return status, payload
