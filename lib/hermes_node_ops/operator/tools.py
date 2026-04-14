"""Operator tool surface — the interface between the operator LLM and hermes-node.

Every tool here is a read or a well-defined hermes-node control plane
operation. The operator never bypasses the ledger, routing policy, or
approval discipline.

Tools are defined as OpenAI function-calling schema dicts for the backend,
with a dispatch function that routes tool calls to implementations.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ..config import Settings
from ..ledger import Ledger
from ..models import APPROVAL_GRANTED, SUPPORTED_TASK_KINDS, TaskSubmission
from ..routing import format_route_table, is_approval_required
from ..workspaces import list_workspaces, read_workspace_manifest
from ..wrapper import format_status_snapshot


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling schema)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_queue_status",
            "description": (
                "Get hermes-node queue overview: task counts by state, "
                "recent tasks, recent runs, deferred tasks, active cooldowns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max items per category (default 5)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_status",
            "description": "Get detailed status for a specific task including latest summary and approval history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to inspect",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_task",
            "description": (
                "Submit a new task to hermes-node for execution. "
                "The task will be queued and executed by the appropriate executor "
                "per routing policy. Tasks requiring approval will need explicit approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": list(SUPPORTED_TASK_KINDS),
                        "description": "Task kind determining executor and risk class",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The task prompt / query",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional short title for the task",
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path (required for repo_read, repo_patch_branch)",
                    },
                },
                "required": ["kind", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_task",
            "description": (
                "Approve a task that is waiting for approval. "
                "Only call this after the human has explicitly confirmed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to approve",
                    },
                    "note": {
                        "type": "string",
                        "description": "Approval note explaining the reason",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_tasks",
            "description": "List the most recent tasks in the hermes-node queue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of tasks (default 10)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_runs",
            "description": "List the most recent execution runs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of runs (default 10)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_workspace",
            "description": "Inspect a task's workspace directory — shows input artifact, run receipts, notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID whose workspace to inspect",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_run_receipt",
            "description": "Read the receipt.json for a specific run in a task workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID",
                    },
                    "run_id": {
                        "type": "string",
                        "description": "The run ID",
                    },
                },
                "required": ["task_id", "run_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_routing_policy",
            "description": "Show the hermes-node routing policy: task kinds, risk classes, executors, approval requirements.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    # -- Repo inspection tools --
    {
        "type": "function",
        "function": {
            "name": "repo_ls",
            "description": "List files and directories in a repository path. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Max directory depth (default 2)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_read_file",
            "description": "Read the contents of a file in a repository. Bounded to first 500 lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to read",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Max lines to return (default 500)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_grep",
            "description": "Search for a pattern in a repository. Returns matching lines with file paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern (grep -rn)",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max matching lines (default 50)",
                    },
                },
                "required": ["pattern", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Get git status for a repository: current branch, dirty state, recent commits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the git repository",
                    },
                },
                "required": ["repo_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Save a note to the current session for future reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "The note content to save",
                    },
                },
                "required": ["note"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

class ToolExecutor:
    """Executes operator tools against hermes-node and the filesystem."""

    def __init__(self, settings: Settings, repo_root: Path, session_id: str = "") -> None:
        self.settings = settings
        self.repo_root = repo_root
        self.session_id = session_id

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        """Dispatch a tool call and return the result as a string."""
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            result = handler(**arguments)
            if isinstance(result, str):
                return result
            return json.dumps(result, indent=2, sort_keys=True, default=str)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    # -- Hermes-node tools --

    def _tool_get_queue_status(self, limit: int = 5) -> dict[str, Any]:
        with Ledger(self.settings.ledger_db) as ledger:
            return format_status_snapshot(ledger, limit=limit)

    def _tool_get_task_status(self, task_id: str) -> dict[str, Any]:
        with Ledger(self.settings.ledger_db) as ledger:
            task = ledger.get_task(task_id)
            result: dict[str, Any] = {"task": task}
            result["approvals"] = ledger.approvals_for_task(task_id)
            summary = ledger.latest_summary(task_id=task_id)
            if summary:
                result["latest_summary"] = summary
            run = ledger.latest_run_for_task(task_id)
            if run:
                result["latest_run"] = run
            result["events"] = ledger.events_for_task(task_id, limit=10)
            return result

    def _tool_submit_task(
        self,
        kind: str,
        prompt: str,
        title: str = "",
        repo_path: str = "",
    ) -> dict[str, Any]:
        require_approval = is_approval_required(kind)
        submission = TaskSubmission(
            kind=kind,
            prompt=prompt,
            title=title,
            provider=self.settings.default_provider,
            model=self.settings.default_model,
            repo_path=repo_path,
            require_approval=require_approval,
            created_by="operator_llm",
        )
        with Ledger(self.settings.ledger_db) as ledger:
            task = ledger.create_task(submission, max_attempts=self.settings.max_attempts)
        return {
            "submitted": True,
            "task_id": task["id"],
            "state": task["state"],
            "kind": task["kind"],
            "approval_required": require_approval,
            "message": (
                f"Task {task['id']} queued as {kind}."
                + (" Requires approval before execution." if require_approval else "")
            ),
        }

    def _tool_approve_task(self, task_id: str, note: str = "") -> dict[str, Any]:
        with Ledger(self.settings.ledger_db) as ledger:
            task = ledger.get_task(task_id)
            if task["state"] != "pending_approval":
                return {
                    "approved": False,
                    "error": f"Task {task_id} is in state '{task['state']}', not pending_approval",
                }
            task = ledger.record_approval(
                task_id,
                status=APPROVAL_GRANTED,
                actor="operator_llm",
                note=note or "Approved by operator",
            )
            return {
                "approved": True,
                "task_id": task_id,
                "new_state": task["state"],
                "message": f"Task {task_id} approved and moved to {task['state']}.",
            }

    def _tool_list_recent_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        with Ledger(self.settings.ledger_db) as ledger:
            return ledger.recent_tasks(limit=limit)

    def _tool_list_recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with Ledger(self.settings.ledger_db) as ledger:
            return ledger.recent_runs(limit=limit)

    def _tool_inspect_workspace(self, task_id: str) -> dict[str, Any]:
        return read_workspace_manifest(self.repo_root, task_id)

    def _tool_read_run_receipt(self, task_id: str, run_id: str) -> dict[str, Any]:
        from ..workspaces import ensure_run_dir
        run_dir = self.repo_root / "state" / "workspaces" / task_id / "runs" / run_id
        receipt_path = run_dir / "receipt.json"
        if not receipt_path.exists():
            return {"error": f"No receipt found at {receipt_path}"}
        try:
            return json.loads(receipt_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return {"error": f"Failed to read receipt: {exc}"}

    def _tool_get_routing_policy(self) -> dict[str, Any]:
        from ..wrapper import available_executors
        avail = available_executors(self.settings)
        return {
            "routes": format_route_table(),
            "available_executors": sorted(avail),
        }

    # -- Repo inspection tools --

    def _tool_repo_ls(self, path: str, max_depth: int = 2) -> dict[str, Any]:
        target = Path(path).expanduser().resolve()
        if not target.exists():
            return {"error": f"Path does not exist: {path}"}
        if not target.is_dir():
            return {"error": f"Not a directory: {path}"}

        entries: list[str] = []
        try:
            result = subprocess.run(
                ["find", str(target), "-maxdepth", str(max_depth), "-not", "-path", "*/.git/*"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            entries = [
                line for line in result.stdout.strip().splitlines()
                if line.strip()
            ][:200]
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"error": f"Failed to list directory: {exc}"}

        return {"path": str(target), "entries": entries, "count": len(entries)}

    def _tool_repo_read_file(self, path: str, max_lines: int = 500) -> dict[str, Any]:
        target = Path(path).expanduser().resolve()
        if not target.exists():
            return {"error": f"File does not exist: {path}"}
        if not target.is_file():
            return {"error": f"Not a file: {path}"}
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"error": f"Failed to read file: {exc}"}

        truncated = len(lines) > max_lines
        content = "\n".join(lines[:max_lines])
        return {
            "path": str(target),
            "lines": min(len(lines), max_lines),
            "total_lines": len(lines),
            "truncated": truncated,
            "content": content,
        }

    def _tool_repo_grep(
        self, pattern: str, path: str, max_results: int = 50,
    ) -> dict[str, Any]:
        target = Path(path).expanduser().resolve()
        if not target.exists():
            return {"error": f"Path does not exist: {path}"}
        try:
            result = subprocess.run(
                ["grep", "-rn", "--include=*.py", "--include=*.md",
                 "--include=*.json", "--include=*.yaml", "--include=*.yml",
                 "--include=*.toml", "--include=*.txt", "--include=*.sh",
                 pattern, str(target)],
                capture_output=True, text=True, check=False, timeout=15,
            )
            matches = result.stdout.strip().splitlines()[:max_results]
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"error": f"Grep failed: {exc}"}

        return {
            "pattern": pattern,
            "path": str(target),
            "matches": matches,
            "count": len(matches),
        }

    def _tool_git_status(self, repo_path: str) -> dict[str, Any]:
        target = Path(repo_path).expanduser().resolve()
        if not target.exists():
            return {"error": f"Path does not exist: {repo_path}"}

        info: dict[str, Any] = {"repo_path": str(target)}
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, cwd=str(target), check=False, timeout=5,
            )
            info["branch"] = branch.stdout.strip()

            status = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=str(target), check=False, timeout=5,
            )
            status_lines = [l for l in status.stdout.strip().splitlines() if l.strip()]
            info["dirty"] = len(status_lines) > 0
            info["changes"] = status_lines[:30]

            log = subprocess.run(
                ["git", "log", "--oneline", "-10"],
                capture_output=True, text=True, cwd=str(target), check=False, timeout=5,
            )
            info["recent_commits"] = log.stdout.strip().splitlines()

        except (subprocess.TimeoutExpired, OSError) as exc:
            info["error"] = f"Git command failed: {exc}"

        return info

    def _tool_save_note(self, note: str) -> dict[str, Any]:
        if not self.session_id:
            return {"error": "No active session for saving notes"}
        from .session import append_note
        append_note(self.repo_root, self.session_id, note)
        return {"saved": True, "note": note}


def get_tool_definitions() -> list[dict[str, Any]]:
    """Return the tool definitions for the chat backend."""
    return TOOL_DEFINITIONS
