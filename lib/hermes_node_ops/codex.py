"""Codex executor adapter for hermes-node.

Wraps the OpenAI Codex CLI as an execution backend.  The wrapper owns
task lifecycle, ledgering, and approval discipline — Codex is just the
code-execution lane.

Requirements for live use:
  - `codex` CLI installed: npm install -g @openai/codex
  - OPENAI_API_KEY set in the environment (or in wrapper .env)
  - Target repo must be a git checkout

If the Codex CLI is not available, the adapter reports unavailability
cleanly so the router can fall back or fail legibly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .jsonl import append_jsonl, utcnow


@dataclass
class CodexResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    branch: str


def codex_bin_path(settings: Settings) -> str | None:
    """Return the path to the codex CLI, or None if not found."""
    explicit = getattr(settings, "codex_bin", "")
    if explicit:
        p = Path(os.path.expanduser(str(explicit)))
        if p.exists():
            return str(p)
    found = shutil.which("codex")
    return found


def codex_available(settings: Settings) -> bool:
    """Check whether the Codex CLI is installed and callable."""
    return codex_bin_path(settings) is not None


def build_codex_command(
    settings: Settings,
    *,
    prompt: str,
    repo_path: str,
    writable: bool = False,
) -> list[str]:
    """Build a codex CLI invocation.

    writable=False → suggest mode (read-only analysis)
    writable=True  → auto-edit mode (can modify files on branch)
    """
    binary = codex_bin_path(settings)
    if binary is None:
        raise RuntimeError("Codex CLI not found")

    approval_mode = "auto-edit" if writable else "suggest"

    cmd = [
        binary,
        "--approval-mode", approval_mode,
        "--quiet",
        "--no-color",
        prompt,
    ]
    return cmd


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

    # Check if branch exists
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
    task_id: str,
    run_id: str,
    writable: bool = False,
    branch: str = "",
) -> CodexResult:
    """Execute a Codex CLI command and capture the result.

    For writable=True, a branch must be provided (never main).
    """
    if writable and not branch:
        raise ValueError("Writable codex execution requires a branch name")
    if writable and branch in ("main", "master"):
        raise ValueError(f"Refusing to run writable codex on protected branch: {branch}")

    if writable and branch:
        ensure_branch(repo_path, branch)

    command = build_codex_command(
        settings,
        prompt=prompt,
        repo_path=repo_path,
        writable=writable,
    )

    env = {**os.environ}
    # Codex needs OPENAI_API_KEY; check wrapper .env or environment
    api_key = env.get("OPENAI_API_KEY", "")
    if not api_key:
        from .config import read_env_file
        wrapper_env = read_env_file(settings.env_file)
        api_key = wrapper_env.get("OPENAI_API_KEY", "")
        if api_key:
            env["OPENAI_API_KEY"] = api_key

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
    )


def make_branch_name(task_id: str) -> str:
    """Generate a predictable branch name for a patch task."""
    return f"hermes-node/{task_id}"
