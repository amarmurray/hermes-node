"""Codex executor adapter for hermes-node.

Wraps the OpenAI Codex CLI as an execution backend. The wrapper owns
task lifecycle, ledgering, and approval discipline; Codex is just the
code-execution lane.

Live prerequisites:
  - `codex` CLI installed: npm install -g @openai/codex
  - one usable auth path:
      * `OPENAI_API_KEY` in env / wrapper `.env`
      * `OPENROUTER_API_KEY` in env / wrapper `.env` / upstream `~/.hermes/.env`
      * or a valid cached `codex login`
  - target repo is a git checkout

If the Codex CLI or auth is unavailable, the adapter reports
unavailability cleanly so the router can fall back or fail legibly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, read_env_file
from .jsonl import append_jsonl, utcnow

OPENROUTER_PROVIDER_ID = "openrouter_custom"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class CodexAuthConfig:
    auth_mode: str
    env: dict[str, str]
    config_overrides: list[str]
    provider_id: str


@dataclass
class CodexResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    branch: str
    auth_mode: str
    provider_id: str


def codex_bin_path(settings: Settings) -> str | None:
    """Return the path to the codex CLI, or None if not found."""
    explicit = getattr(settings, "codex_bin", "")
    if explicit:
        path = Path(os.path.expanduser(str(explicit)))
        if path.exists():
            return str(path)
    return shutil.which("codex")


def _merged_secret_values(settings: Settings) -> dict[str, str]:
    merged: dict[str, str] = {}
    for source in (
        read_env_file(settings.upstream_env_file),
        read_env_file(settings.env_file),
        os.environ,
    ):
        for key, value in source.items():
            if value:
                merged[key] = value
    return merged


def codex_login_authenticated(settings: Settings) -> bool:
    """Check whether Codex has a usable cached login session."""
    binary = codex_bin_path(settings)
    if binary is None:
        return False
    try:
        result = subprocess.run(
            [binary, "login", "status"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def resolve_codex_auth(settings: Settings, requested_provider: str = "") -> CodexAuthConfig | None:
    """Resolve a usable Codex auth + provider configuration."""
    secrets = _merged_secret_values(settings)
    requested = requested_provider.strip().lower()
    openai_key = secrets.get("OPENAI_API_KEY", "")
    openrouter_key = secrets.get("OPENROUTER_API_KEY", "")

    def openai_env_auth() -> CodexAuthConfig | None:
        if not openai_key:
            return None
        return CodexAuthConfig(
            auth_mode="openai_env",
            env={"OPENAI_API_KEY": openai_key},
            config_overrides=[],
            provider_id="openai",
        )

    def openrouter_env_auth() -> CodexAuthConfig | None:
        if not openrouter_key:
            return None
        return CodexAuthConfig(
            auth_mode="openrouter_env",
            env={"OPENROUTER_API_KEY": openrouter_key},
            config_overrides=[
                'model_provider="openrouter_custom"',
                'model_providers.openrouter_custom.name="OpenRouter"',
                f'model_providers.openrouter_custom.base_url="{OPENROUTER_BASE_URL}"',
                'model_providers.openrouter_custom.env_key="OPENROUTER_API_KEY"',
                'model_providers.openrouter_custom.wire_api="responses"',
            ],
            provider_id=OPENROUTER_PROVIDER_ID,
        )

    def cached_login_auth() -> CodexAuthConfig | None:
        if not codex_login_authenticated(settings):
            return None
        return CodexAuthConfig(
            auth_mode="codex_login",
            env={},
            config_overrides=[],
            provider_id="openai",
        )

    if requested in ("openrouter", OPENROUTER_PROVIDER_ID):
        return openrouter_env_auth()
    if requested == "openai":
        return openai_env_auth() or cached_login_auth()

    return openai_env_auth() or openrouter_env_auth() or cached_login_auth()


def codex_available(settings: Settings, requested_provider: str = "") -> bool:
    """Check whether the Codex CLI is installed and usable for this task."""
    return codex_bin_path(settings) is not None and (
        resolve_codex_auth(settings, requested_provider=requested_provider) is not None
    )


def build_codex_command(
    settings: Settings,
    *,
    prompt: str,
    repo_path: str,
    model: str,
    provider: str = "",
    writable: bool = False,
) -> tuple[list[str], CodexAuthConfig]:
    """Build a current Codex CLI invocation."""
    binary = codex_bin_path(settings)
    if binary is None:
        raise RuntimeError("Codex CLI not found")
    auth = resolve_codex_auth(settings, requested_provider=provider)
    if auth is None:
        raise RuntimeError("Codex CLI is installed but no usable auth is configured")

    sandbox = "workspace-write" if writable else "read-only"
    command = [
        binary,
        "-a", "never",
        "-s", sandbox,
        "exec",
    ]
    for override in auth.config_overrides:
        command.extend(["-c", override])
    if model:
        command.extend(["-m", model])
    command.extend(["--color", "never", "-C", repo_path, prompt])
    return command, auth


def ensure_branch(repo_path: str, branch_name: str) -> str:
    """Create or switch to a named branch. Returns the branch name.

    Never operates on main/master. If already on the target branch,
    this is a no-op.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=repo_path, check=False,
    )
    current = result.stdout.strip()
    if current == branch_name:
        return branch_name

    check = subprocess.run(
        ["git", "rev-parse", "--verify", branch_name],
        capture_output=True, text=True, cwd=repo_path, check=False,
    )
    if check.returncode == 0:
        subprocess.run(
            ["git", "checkout", branch_name],
            capture_output=True, text=True, cwd=repo_path, check=True,
        )
    else:
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            capture_output=True, text=True, cwd=repo_path, check=True,
        )
    return branch_name


def run_codex(
    settings: Settings,
    *,
    prompt: str,
    repo_path: str,
    provider: str,
    model: str,
    task_id: str,
    run_id: str,
    writable: bool = False,
    branch: str = "",
) -> CodexResult:
    """Execute a Codex CLI command and capture the result."""
    if writable and not branch:
        raise ValueError("Writable codex execution requires a branch name")
    if writable and branch in ("main", "master"):
        raise ValueError(f"Refusing to run writable codex on protected branch: {branch}")

    if writable and branch:
        ensure_branch(repo_path, branch)

    command, auth = build_codex_command(
        settings,
        prompt=prompt,
        repo_path=repo_path,
        model=model,
        provider=provider,
        writable=writable,
    )

    env = {**os.environ, **auth.env}
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=repo_path,
        stdin=subprocess.DEVNULL,
        env=env,
        check=False,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)

    append_jsonl(
        settings.log_jsonl,
        {
            "timestamp": utcnow(),
            "action": "codex_run",
            "command": command,
            "cwd": repo_path,
            "task_id": task_id,
            "run_id": run_id,
            "exit_code": completed.returncode,
            "duration_ms": duration_ms,
            "writable": writable,
            "branch": branch,
            "auth_mode": auth.auth_mode,
            "provider_id": auth.provider_id,
            "stdout_preview": completed.stdout[:400],
            "stderr_preview": completed.stderr[:400],
        },
    )

    return CodexResult(
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_ms=duration_ms,
        branch=branch,
        auth_mode=auth.auth_mode,
        provider_id=auth.provider_id,
    )


def make_branch_name(task_id: str) -> str:
    """Generate a predictable branch name for a patch task."""
    return f"hermes-node/{task_id}"
