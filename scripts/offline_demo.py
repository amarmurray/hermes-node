#!/usr/bin/env python3
"""Exercise the real task lifecycle with a deterministic, offline fake provider.

No model, API key, network connection, subprocess, or repository mutation is
used. Only the executor boundary is replaced; task/approval persistence,
rate-limit handling, queue selection, and run artifacts use the application.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.config import Settings
from hermes_node_ops.ledger import Ledger
from hermes_node_ops.models import APPROVAL_GRANTED, TaskSubmission
from hermes_node_ops.routing import EXECUTOR_HERMES_CHAT
from hermes_node_ops.wrapper import CommandResult, execute_task


FIXTURE_PROMPT = (
    "OFFLINE DEMO: summarize these fictional work orders. "
    "WO-001: completed; WO-002: completed; WO-003: waiting for a replacement part. "
    "This input is a synthetic fixture, not a customer workflow."
)
FIXTURE_RESPONSE = (
    "OFFLINE MOCK RESULT: 2 fictional work orders completed; "
    "1 awaits a replacement part. This response is scripted, not model-generated.\n"
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"Offline demo verification failed: {message}")


def _settings(output_dir: Path) -> Settings:
    """Construct isolated settings without loading .env or provider credentials."""
    placeholder = output_dir / "mock-hermes-not-executed.txt"
    placeholder.write_text(
        "Non-executable placeholder. The demo records a command but never runs it.\n",
        encoding="utf-8",
    )
    return Settings(
        repo_root=output_dir,
        env_file=output_dir / "unused.env",
        upstream_home=output_dir / "unused-upstream-home",
        upstream_repo=output_dir / "unused-upstream-repo",
        hermes_bin=placeholder,
        ledger_db=output_dir / "state" / "hermes-node.db",
        log_jsonl=output_dir / "logs" / "hermes-node.jsonl",
        service_user="offline-demo",
        default_provider="offline-demo",
        default_model="deterministic-fixture-v1",
        default_max_turns=2,
        poll_interval_seconds=1,
        source_tag="offline-demo",
        max_attempts=3,
        base_backoff_seconds=1,
        max_backoff_seconds=1,
        codex_bin="",
        telegram_bot_token="",
        telegram_allowed_chat_ids=(),
        telegram_poll_timeout=1,
        telegram_alert_poll_seconds=1,
    )


class ScriptedProvider:
    """Return a fake 429 once, then a fixed response. Never execute the command."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, settings: Settings, command: list[str], **kwargs) -> CommandResult:
        self.calls += 1
        if self.calls == 1:
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="",
                stderr="OFFLINE DEMO: simulated HTTP 429; no request was sent.\n",
                duration_ms=0,
            )
        if self.calls == 2:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=FIXTURE_RESPONSE,
                stderr="",
                duration_ms=0,
            )
        raise RuntimeError("The scripted provider permits exactly two attempts")


def run_demo(output_dir: Path, emit: Callable[[str], None] = print) -> dict:
    """Run in a new/empty directory and leave inspectable, explicitly mock artifacts."""
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError("Choose a new or empty output directory; existing files are not overwritten")
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = _settings(output_dir)
    settings.ensure_wrapper_dirs()
    provider = ScriptedProvider()
    transitions: list[str] = []

    emit("Hermes-node offline demonstration")
    emit("Mode: deterministic mock provider; no model, credentials, network, or subprocess")

    # Do not probe installed executors: probing Codex could inspect real auth.
    # The selected route is real; the provider boundary is explicitly mocked.
    with patch(
        "hermes_node_ops.wrapper.available_executors",
        return_value={EXECUTOR_HERMES_CHAT},
    ):
        with Ledger(settings.ledger_db) as ledger:
            task = ledger.create_task(
                TaskSubmission(
                    kind="summary",
                    title="Synthetic work-order summary (offline demo)",
                    prompt=FIXTURE_PROMPT,
                    provider=settings.default_provider,
                    model=settings.default_model,
                    require_approval=True,
                    created_by="offline-demo",
                    metadata={"execution_mode": "deterministic_mock", "synthetic_input": True},
                ),
                max_attempts=settings.max_attempts,
            )
            task_id = task["id"]
            transitions.append(task["state"])
            _require(task["state"] == "pending_approval", "task must await approval")
            _require(ledger.next_runnable_task() is None, "unapproved task must not enter the queue")
            emit("1. Created durable task: pending_approval")

            try:
                execute_task(settings, ledger, task_id, command_runner=provider)
            except ValueError as exc:
                _require("awaiting approval" in str(exc), "unexpected approval error")
            else:
                raise RuntimeError("An unapproved task executed")
            _require(provider.calls == 0, "approval gate must run before the provider")
            _require(not ledger.recent_runs(), "blocked execution must not create a run")
            emit("2. Execution blocked before provider invocation: 0 calls")

            approved = ledger.record_approval(
                task_id,
                status=APPROVAL_GRANTED,
                actor="demo-reviewer (simulated)",
                note="Scripted approval for a synthetic fixture; no human approval is claimed.",
                data={"execution_mode": "deterministic_mock"},
            )
            transitions.append(approved["state"])
            _require(approved["state"] == "pending", "approval should make the task pending")
            emit("3. Recorded simulated reviewer approval: pending")

            first = execute_task(settings, ledger, task_id, command_runner=provider)
            transitions.append(first.state)
            _require(first.state == "deferred_rate_limit", "fake 429 should defer the task")
            deferred = ledger.get_task(task_id)
            _require(deferred["attempt_count"] == 1, "retry count should persist")
            retry_at = deferred["next_attempt_at"]
            _require(retry_at is not None, "deferred task needs a retry time")
            _require(ledger.next_runnable_task() is None, "cooldown should prevent an immediate retry")
            emit("4. Simulated HTTP 429: deferred_rate_limit; first receipt saved")

        # Open a new connection to verify state survives closing the first one.
        with Ledger(settings.ledger_db) as ledger:
            restored = ledger.get_task(task_id)
            _require(restored["state"] == "deferred_rate_limit", "deferred state must survive reopen")
            _require(restored["approval_state"] == APPROVAL_GRANTED, "approval must survive reopen")
            _require(len(ledger.recent_runs()) == 1, "first run must survive reopen")
            emit("5. Closed and reopened SQLite: task, approval, and first run preserved")

            # Use the real queue/cooldown behavior with a short demo-only delay.
            # Production defaults are unchanged (30-second base, 15-minute cap).
            remaining = (datetime.fromisoformat(retry_at) - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                time.sleep(remaining + 0.02)
            runnable = ledger.next_runnable_task()
            _require(runnable is not None and runnable["id"] == task_id, "retry should be selected when due")
            second = execute_task(settings, ledger, task_id, command_runner=provider)
            transitions.append(second.state)
            _require(second.state == "done", "scripted retry should complete")
            _require(second.summary == FIXTURE_RESPONSE.strip(), "summary should preserve the mock label")

            runs = sorted(ledger.recent_runs(), key=lambda row: row["started_at"])
            approvals = ledger.approvals_for_task(task_id)
            events = list(reversed(ledger.events_for_task(task_id, limit=100)))
            _require([row["exit_code"] for row in runs] == [1, 0], "failed and successful runs must both remain")
            receipts = []
            for run in runs:
                path = Path(task["workspace_dir"]) / "runs" / run["id"] / "receipt.json"
                receipt = json.loads(path.read_text(encoding="utf-8"))
                _require(receipt["provider"] == "offline-demo", "receipt must identify the mock provider")
                receipts.append(str(path.relative_to(output_dir)))
            emit("6. Cooldown elapsed; queue selected retry: done; both run receipts retained")

    result = {
        "execution_mode": "deterministic_mock",
        "live_model_calls": 0,
        "real_repository_tasks_completed": 0,
        "approval_actor_is_simulated": True,
        "task_id": task_id,
        "state_transitions": transitions,
        "final_state": second.state,
        "mock_provider_calls": provider.calls,
        "run_exit_codes": [row["exit_code"] for row in runs],
        "persisted_after_reopen": True,
        "receipts": receipts,
        "approvals": approvals,
        "events": events,
        "result": second.summary,
        "notes": [
            "The Hermes route is exercised with an injected fake runner; its recorded command is never executed.",
            "The scripted provider's zero duration is a fixture value, not a latency benchmark.",
            "Only a new/empty output directory is written; no live repository is inspected or changed.",
        ],
    }
    summary_path = output_dir / "demo-summary.json"
    summary_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    emit(f"Artifacts: {output_dir}")
    emit(f"Summary:   {summary_path}")
    emit("Verified an offline infrastructure demonstration, not a real repository task or model capability.")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New or empty artifact directory (default: a new temporary directory)")
    args = parser.parse_args()
    output_dir = args.output or Path(tempfile.mkdtemp(prefix="hermes-node-demo-"))
    try:
        run_demo(output_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Offline demo failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
