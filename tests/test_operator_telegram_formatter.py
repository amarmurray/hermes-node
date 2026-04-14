"""Tests for the Telegram formatter helpers."""

from __future__ import annotations

import json

from hermes_node_ops.operator.telegram.formatter import (
    _short,
    format_alert,
    format_assistant_response,
    format_pending_approvals,
    format_queue_status,
    format_run_list,
    format_task_list,
    format_task_status,
    format_tool_result_compact,
)


def test_short_preserves_short_ids() -> None:
    assert _short("task_abc") == "task_abc"
    assert _short("") == "?"


def test_short_abbreviates_long_ids() -> None:
    ident = "task_" + "a" * 40
    out = _short(ident)
    assert ".." in out
    assert out.startswith("task_")
    assert out.endswith(ident[-10:])


def test_format_queue_status_empty() -> None:
    out = format_queue_status({})
    assert "queue: empty" in out


def test_format_queue_status_populated() -> None:
    payload = {
        "counts": {"queued": 2, "running": 1, "succeeded": 5},
        "deferred_tasks": [{"id": "task_1", "next_attempt_at": "2026-04-14T00:00:00Z"}],
        "active_cooldowns": [{"key": "provider"}],
        "recent_tasks": [{"id": "task_2", "state": "running", "kind": "repo_read"}],
        "recent_runs": [
            {"id": "run_1", "state": "succeeded", "executor": "hermes", "exit_code": 0},
            {"id": "run_2", "state": "failed", "executor": "hermes", "exit_code": 2},
        ],
    }
    out = format_queue_status(payload)
    assert "queued=2" in out
    assert "running=1" in out
    assert "deferred: 1" in out
    assert "cooldowns: 1" in out
    assert "recent tasks:" in out
    assert "recent runs:" in out
    assert "ok" in out
    assert "exit=2" in out


def test_format_task_status() -> None:
    payload = {
        "task": {
            "id": "task_abc",
            "kind": "repo_read",
            "state": "succeeded",
            "attempt_count": 2,
            "prompt_preview": "look at README",
        },
        "latest_summary": {"content": "All good."},
        "latest_run": {"id": "run_1", "state": "succeeded", "exit_code": 0},
    }
    out = format_task_status(payload)
    assert "task: task_abc" in out
    assert "attempts: 2" in out
    assert "All good." in out
    assert "latest run:" in out


def test_format_task_list_empty_and_populated() -> None:
    assert "no tasks" in format_task_list([], header="tasks")
    out = format_task_list(
        [{"id": "t1", "state": "queued", "title": "do thing"}],
        header="queued",
    )
    assert "queued:" in out
    assert "do thing" in out


def test_format_pending_approvals_filters() -> None:
    tasks = [
        {"id": "t1", "state": "pending_approval", "kind": "repo_patch_branch"},
        {"id": "t2", "state": "succeeded"},
    ]
    out = format_pending_approvals(tasks)
    assert "pending approval (1)" in out
    assert "t1" in out
    assert "t2" not in out
    assert "/approve" in out


def test_format_pending_approvals_none() -> None:
    assert "no tasks awaiting approval" in format_pending_approvals([])


def test_format_run_list() -> None:
    assert "no runs" in format_run_list([])
    out = format_run_list([{"id": "r1", "state": "succeeded", "executor": "hermes", "exit_code": 0}])
    assert "r1" in out
    assert "ok" in out


def test_format_tool_result_compact_dispatch_queue() -> None:
    result = json.dumps({"counts": {"queued": 3}})
    out = format_tool_result_compact("get_queue_status", result)
    assert "queued=3" in out


def test_format_tool_result_compact_error_passthrough() -> None:
    result = json.dumps({"error": "nope"})
    out = format_tool_result_compact("get_task_status", result)
    assert "error: nope" in out


def test_format_tool_result_compact_submit_task() -> None:
    result = json.dumps({"task_id": "task_xyz", "state": "queued"})
    out = format_tool_result_compact("submit_task", result)
    assert "submitted task_xyz" in out


def test_format_tool_result_compact_invalid_json() -> None:
    out = format_tool_result_compact("whatever", "not-json")
    assert out.startswith("[whatever]")


def test_format_assistant_response_strips_thinking() -> None:
    out = format_assistant_response("<think>inner</think>  final answer  ")
    assert "<think>" not in out
    assert "final answer" in out


def test_format_assistant_response_caps_length() -> None:
    out = format_assistant_response("x" * 5000)
    assert len(out) <= 3504
    assert out.endswith("...")


def test_format_alert_shapes() -> None:
    assert "FAILED: task_1" in format_alert("task.failed", {"task_id": "task_1", "message": "boom"})
    assert "DEFERRED" in format_alert(
        "task.deferred_rate_limit", {"task_id": "task_2", "message": "wait"}
    )
    assert "NEEDS APPROVAL" in format_alert(
        "task.pending_approval", {"task_id": "task_3"}
    )
    assert "/approve task_3" in format_alert(
        "task.pending_approval", {"task_id": "task_3"}
    )
