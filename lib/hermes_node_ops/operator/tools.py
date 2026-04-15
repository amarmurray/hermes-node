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
from .harness import (
    complete_todo_item,
    ensure_harness_layout,
    list_subagents,
    read_plan,
    read_scratch,
    read_subagent_result,
    spawn_subagent,
    update_plan,
    update_todo_item,
    write_plan,
    write_scratch,
    write_todo_items,
)
from .harness.specialists import SPECIALIST_ROLES


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
    {
        "type": "function",
        "function": {
            "name": "write_plan",
            "description": "Write the current session plan to plan.md.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Full markdown plan content",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_plan",
            "description": "Read the current session plan.md.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "Append an update block to the current session plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Plan update content to append",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_todo",
            "description": "Write or append checklist items in todo.md.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Checklist item texts",
                    },
                    "replace": {
                        "type": "boolean",
                        "description": "Replace the current todo instead of appending",
                    },
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_todo",
            "description": "Update the text for one todo item by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Todo item id such as t1",
                    },
                    "text": {
                        "type": "string",
                        "description": "Replacement text",
                    },
                },
                "required": ["item_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_todo_item",
            "description": "Mark one todo item complete by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Todo item id such as t1",
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional completion note",
                    },
                },
                "required": ["item_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_scratch",
            "description": "Write a scratchpad file for one step of work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "step_id": {
                        "type": "string",
                        "description": "Scratch step identifier",
                    },
                    "content": {
                        "type": "string",
                        "description": "Scratch markdown content",
                    },
                },
                "required": ["step_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_scratch",
            "description": "Read a scratchpad file by step id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "step_id": {
                        "type": "string",
                        "description": "Scratch step identifier",
                    },
                },
                "required": ["step_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": "Run a bounded specialist subagent under the current session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": list(SPECIALIST_ROLES),
                        "description": "Specialist role",
                    },
                    "goal": {
                        "type": "string",
                        "description": "Focused bounded objective for the subagent",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional additional context",
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Optional repository path for repo-oriented work",
                    },
                },
                "required": ["role", "goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_subagent_result",
            "description": "Read a completed subagent result by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subagent_id": {
                        "type": "string",
                        "description": "Subagent id",
                    },
                },
                "required": ["subagent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_subagents",
            "description": "List the current session's subagents.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
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
        if session_id:
            ensure_harness_layout(repo_root, session_id)

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

    @staticmethod
    def _preview(text: str, limit: int = 240) -> str:
        text = text.strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _task_view(self, task: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": task["id"],
            "kind": task["kind"],
            "state": task["state"],
            "approval_state": task.get("approval_state", ""),
            "title": task.get("title", ""),
            "created_by": task.get("created_by", ""),
            "provider": task.get("provider", ""),
            "model": task.get("model", ""),
            "repo_path": task.get("repo_path", ""),
            "latest_run_id": task.get("latest_run_id", ""),
            "attempt_count": task.get("attempt_count", 0),
            "next_attempt_at": task.get("next_attempt_at"),
            "created_at": task.get("created_at", ""),
            "updated_at": task.get("updated_at", ""),
            "prompt_preview": self._preview(task.get("prompt", ""), 180),
        }

    def _run_view(self, run: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": run["id"],
            "task_id": run["task_id"],
            "state": run["state"],
            "executor": run.get("executor", ""),
            "provider": run.get("provider", ""),
            "model": run.get("model", ""),
            "exit_code": run.get("exit_code"),
            "duration_ms": run.get("duration_ms"),
            "created_at": run.get("created_at", ""),
            "started_at": run.get("started_at", ""),
            "finished_at": run.get("finished_at", ""),
            "working_dir": run.get("working_dir", ""),
            "stdout_preview": self._preview(run.get("stdout", ""), 180),
            "stderr_preview": self._preview(run.get("stderr", ""), 180),
        }

    def _summary_view(self, summary: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": summary["id"],
            "task_id": summary["task_id"],
            "run_id": summary.get("run_id", ""),
            "summary_type": summary.get("summary_type", ""),
            "created_at": summary.get("created_at", ""),
            "content": self._preview(summary.get("content", ""), 500),
            "metadata": summary.get("metadata", {}),
        }

    def _approval_view(self, approval: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": approval["id"],
            "task_id": approval["task_id"],
            "status": approval.get("status", ""),
            "actor": approval.get("actor", ""),
            "decided_at": approval.get("decided_at", ""),
            "note": self._preview(approval.get("note", ""), 180),
        }

    def _event_view(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": event["id"],
            "task_id": event.get("task_id", ""),
            "run_id": event.get("run_id"),
            "event_type": event.get("event_type", ""),
            "level": event.get("level", ""),
            "created_at": event.get("created_at", ""),
            "message": self._preview(event.get("message", ""), 220),
        }

    # -- Hermes-node tools --

    def _tool_get_queue_status(self, limit: int = 5) -> dict[str, Any]:
        with Ledger(self.settings.ledger_db) as ledger:
            snap = format_status_snapshot(ledger, limit=limit)
            return {
                "counts": snap.get("counts", {}),
                "deferred_tasks": [
                    {
                        "id": t["id"],
                        "kind": t["kind"],
                        "state": t["state"],
                        "approval_state": t.get("approval_state", ""),
                        "next_attempt_at": t.get("next_attempt_at"),
                    }
                    for t in snap.get("deferred_tasks", [])
                ],
                "recent_tasks": [
                    {
                        "id": t["id"],
                        "kind": t["kind"],
                        "state": t["state"],
                        "approval_state": t.get("approval_state", ""),
                        "title": t.get("title", ""),
                        "created_at": t.get("created_at", ""),
                        "latest_run_id": t.get("latest_run_id", ""),
                    }
                    for t in snap.get("recent_tasks", [])
                ],
                "recent_runs": [
                    {
                        "id": r["id"],
                        "task_id": r["task_id"],
                        "state": r["state"],
                        "executor": r.get("executor", ""),
                        "provider": r.get("provider", ""),
                        "model": r.get("model", ""),
                        "exit_code": r.get("exit_code"),
                        "finished_at": r.get("finished_at", ""),
                    }
                    for r in snap.get("recent_runs", [])
                ],
                "active_cooldowns": snap.get("active_cooldowns", []),
                "recent_rate_limit_events": [
                    {
                        "task_id": e.get("task_id", ""),
                        "created_at": e.get("created_at", ""),
                        "message": self._preview(e.get("message", ""), 120),
                    }
                    for e in snap.get("recent_rate_limit_events", [])
                ],
            }

    def _tool_get_task_status(self, task_id: str) -> dict[str, Any]:
        with Ledger(self.settings.ledger_db) as ledger:
            task = ledger.get_task(task_id)
            result: dict[str, Any] = {"task": self._task_view(task)}
            result["approvals"] = [
                self._approval_view(a) for a in ledger.approvals_for_task(task_id)
            ]
            summary = ledger.latest_summary(task_id=task_id)
            if summary:
                result["latest_summary"] = self._summary_view(summary)
            run = ledger.latest_run_for_task(task_id)
            if run:
                result["latest_run"] = self._run_view(run)
            result["events"] = [
                self._event_view(e) for e in ledger.events_for_task(task_id, limit=10)
            ]
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
            return [self._task_view(t) for t in ledger.recent_tasks(limit=limit)]

    def _tool_list_recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with Ledger(self.settings.ledger_db) as ledger:
            return [self._run_view(r) for r in ledger.recent_runs(limit=limit)]

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

    def _tool_write_plan(self, content: str) -> dict[str, Any]:
        session_id = self._require_session()
        plan = write_plan(self.repo_root, session_id, content)
        return {"written": True, "content": plan}

    def _tool_read_plan(self) -> dict[str, Any]:
        session_id = self._require_session()
        return {"content": read_plan(self.repo_root, session_id)}

    def _tool_update_plan(self, content: str) -> dict[str, Any]:
        session_id = self._require_session()
        plan = update_plan(self.repo_root, session_id, content)
        return {"updated": True, "content": plan}

    def _tool_write_todo(self, items: list[str], replace: bool = False) -> dict[str, Any]:
        session_id = self._require_session()
        todo = write_todo_items(self.repo_root, session_id, items, replace=replace)
        return {"written": True, "items": todo}

    def _tool_update_todo(self, item_id: str, text: str) -> dict[str, Any]:
        session_id = self._require_session()
        item = update_todo_item(self.repo_root, session_id, item_id, text)
        return {"updated": True, "item": item}

    def _tool_complete_todo_item(self, item_id: str, note: str = "") -> dict[str, Any]:
        session_id = self._require_session()
        item = complete_todo_item(self.repo_root, session_id, item_id, note=note)
        return {"completed": True, "item": item}

    def _tool_write_scratch(self, step_id: str, content: str) -> dict[str, Any]:
        session_id = self._require_session()
        return write_scratch(self.repo_root, session_id, step_id, content)

    def _tool_read_scratch(self, step_id: str) -> dict[str, Any]:
        session_id = self._require_session()
        return read_scratch(self.repo_root, session_id, step_id)

    def _tool_spawn_subagent(
        self,
        role: str,
        goal: str,
        context: str = "",
        repo_path: str = "",
    ) -> dict[str, Any]:
        session_id = self._require_session()
        return spawn_subagent(
            self.settings,
            self.repo_root,
            session_id,
            role=role,
            goal=goal,
            context=context,
            repo_path=repo_path,
        )

    def _tool_read_subagent_result(self, subagent_id: str) -> dict[str, Any]:
        session_id = self._require_session()
        return read_subagent_result(self.repo_root, session_id, subagent_id)

    def _tool_list_subagents(self) -> list[dict[str, Any]]:
        session_id = self._require_session()
        return list_subagents(self.repo_root, session_id)

    def _require_session(self) -> str:
        if not self.session_id:
            raise ValueError("No active session for harness operation")
        return self.session_id


def _compact_property_schema(schema: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("type", "enum", "items", "default"):
        if key in schema:
            compact[key] = schema[key]
    return compact


def _compact_tool_definition(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool["function"]
    params = function.get("parameters", {})
    properties = params.get("properties", {})
    compact_properties = {
        name: _compact_property_schema(prop)
        for name, prop in properties.items()
    }
    compact_params: dict[str, Any] = {
        "type": params.get("type", "object"),
        "properties": compact_properties,
        "required": params.get("required", []),
    }
    return {
        "type": tool.get("type", "function"),
        "function": {
            "name": function["name"],
            "description": function["name"],
            "parameters": compact_params,
        },
    }


def get_tool_definitions(*, compact: bool = True) -> list[dict[str, Any]]:
    """Return tool definitions sized for the operator backend.

    The compact form is the default because the always-on local operator
    uses a small OSS model over Ollama on a VPS. Keeping tool schemas
    tight materially improves tool-call reliability.
    """
    if not compact:
        return TOOL_DEFINITIONS
    return [_compact_tool_definition(tool) for tool in TOOL_DEFINITIONS]
