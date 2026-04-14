from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .jsonl import utcnow


def workspace_root(settings_repo_root: Path) -> Path:
    return settings_repo_root / "state" / "workspaces"


def task_workspace(settings_repo_root: Path, task_id: str) -> Path:
    return workspace_root(settings_repo_root) / task_id


def resolve_workspace_path(
    settings_repo_root: Path,
    task_id: str,
    *,
    workspace_dir: str | Path = "",
) -> Path:
    if workspace_dir:
        return Path(workspace_dir).expanduser().resolve()
    return task_workspace(settings_repo_root, task_id).resolve()


def ensure_workspace(
    settings_repo_root: Path,
    task_id: str,
    *,
    workspace_dir: str | Path = "",
) -> Path:
    ws = resolve_workspace_path(settings_repo_root, task_id, workspace_dir=workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "runs").mkdir(exist_ok=True)
    (ws / "notes").mkdir(exist_ok=True)
    return ws


def ensure_run_dir(
    settings_repo_root: Path,
    task_id: str,
    run_id: str,
    *,
    workspace_dir: str | Path = "",
) -> Path:
    ws = ensure_workspace(settings_repo_root, task_id, workspace_dir=workspace_dir)
    run_dir = ws / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_input_artifact(
    settings_repo_root: Path,
    task_id: str,
    *,
    kind: str,
    prompt: str,
    provider: str = "",
    model: str = "",
    repo_path: str = "",
    metadata: dict[str, Any] | None = None,
    created_at: str = "",
    workspace_dir: str | Path = "",
) -> Path:
    ws = ensure_workspace(settings_repo_root, task_id, workspace_dir=workspace_dir)
    input_path = ws / "input.md"
    if input_path.exists():
        return input_path

    lines = [
        f"# Task {task_id}",
        "",
        "## Submission",
        "",
        f"- kind: {kind}",
        f"- created_at: {created_at or utcnow()}",
    ]
    if provider:
        lines.append(f"- provider: {provider}")
    if model:
        lines.append(f"- model: {model}")
    if repo_path:
        lines.append(f"- repo_path: {repo_path}")
    lines.extend(["", "## Prompt", "", prompt.rstrip(), ""])
    if metadata:
        lines.extend(["## Metadata", "", "```json", json.dumps(metadata, indent=2, sort_keys=True), "```", ""])

    input_path.write_text("\n".join(lines), encoding="utf-8")
    return input_path


def write_run_output(
    settings_repo_root: Path,
    task_id: str,
    run_id: str,
    *,
    stdout: str,
    stderr: str,
    workspace_dir: str | Path = "",
) -> Path:
    run_dir = ensure_run_dir(settings_repo_root, task_id, run_id, workspace_dir=workspace_dir)
    (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    return run_dir


def write_run_receipt(
    settings_repo_root: Path,
    task_id: str,
    run_id: str,
    *,
    exit_code: int | None,
    duration_ms: int | None,
    executor: str,
    provider: str,
    model: str,
    state: str,
    created_at: str = "",
    started_at: str = "",
    finished_at: str = "",
    working_dir: str = "",
    command: list[str] | None = None,
    hermes_session_id: str = "",
    branch: str = "",
    extra: dict[str, Any] | None = None,
    workspace_dir: str | Path = "",
) -> Path:
    run_dir = ensure_run_dir(settings_repo_root, task_id, run_id, workspace_dir=workspace_dir)
    receipt: dict[str, Any] = {
        "task_id": task_id,
        "run_id": run_id,
        "receipt_written_at": utcnow(),
        "created_at": created_at,
        "started_at": started_at,
        "finished_at": finished_at or ("" if exit_code is None else utcnow()),
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "executor": executor,
        "provider": provider,
        "model": model,
        "state": state,
        "working_dir": working_dir,
        "command": list(command or []),
    }
    if hermes_session_id:
        receipt["hermes_session_id"] = hermes_session_id
    if branch:
        receipt["branch"] = branch
    if extra:
        receipt.update(extra)

    receipt_path = run_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt_path


def write_worktree_metadata(
    settings_repo_root: Path,
    task_id: str,
    *,
    repo_path: str,
    branch: str = "",
    worktree_path: str = "",
    extra: dict[str, Any] | None = None,
    workspace_dir: str | Path = "",
) -> Path:
    ws = ensure_workspace(settings_repo_root, task_id, workspace_dir=workspace_dir)
    path = ws / "worktree.json"

    payload: dict[str, Any] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}

    payload.setdefault("created_at", utcnow())
    payload["updated_at"] = utcnow()
    payload["repo_path"] = repo_path
    if branch:
        payload["branch"] = branch
    if worktree_path:
        payload["worktree_path"] = worktree_path
    if extra:
        payload.update(extra)

    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_workspace_manifest(
    settings_repo_root: Path,
    task_id: str,
    *,
    workspace_dir: str | Path = "",
) -> dict[str, Any]:
    ws = resolve_workspace_path(settings_repo_root, task_id, workspace_dir=workspace_dir)
    manifest: dict[str, Any] = {
        "task_id": task_id,
        "workspace_path": str(ws),
        "exists": ws.exists(),
    }
    if not ws.exists():
        return manifest

    input_path = ws / "input.md"
    worktree_path = ws / "worktree.json"
    manifest["input_path"] = str(input_path)
    manifest["has_input"] = input_path.exists()
    manifest["worktree_path"] = str(worktree_path)
    manifest["has_worktree_meta"] = worktree_path.exists()
    if worktree_path.exists():
        try:
            manifest["worktree"] = json.loads(worktree_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest["worktree"] = None

    runs_dir = ws / "runs"
    runs: list[dict[str, Any]] = []
    if runs_dir.exists():
        for run_dir in sorted(runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            receipt_path = run_dir / "receipt.json"
            run_info: dict[str, Any] = {
                "run_id": run_dir.name,
                "run_path": str(run_dir),
                "stdout_path": str(run_dir / "stdout.txt"),
                "stderr_path": str(run_dir / "stderr.txt"),
                "receipt_path": str(receipt_path),
                "has_stdout": (run_dir / "stdout.txt").exists(),
                "has_stderr": (run_dir / "stderr.txt").exists(),
                "has_receipt": receipt_path.exists(),
            }
            if receipt_path.exists():
                try:
                    run_info["receipt"] = json.loads(receipt_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    run_info["receipt"] = None
            runs.append(run_info)
    manifest["runs"] = runs

    notes_dir = ws / "notes"
    manifest["notes"] = sorted(f.name for f in notes_dir.iterdir() if f.is_file()) if notes_dir.exists() else []
    return manifest


def cleanup_workspace(
    settings_repo_root: Path,
    task_id: str,
    *,
    workspace_dir: str | Path = "",
) -> bool:
    ws = resolve_workspace_path(settings_repo_root, task_id, workspace_dir=workspace_dir)
    if not ws.exists():
        return False
    shutil.rmtree(ws)
    return True


def list_workspaces(settings_repo_root: Path) -> list[str]:
    root = workspace_root(settings_repo_root)
    if not root.exists():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir())
