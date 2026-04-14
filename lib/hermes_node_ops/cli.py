from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .config import Settings
from .ledger import Ledger
from .models import APPROVAL_GRANTED, SUPPORTED_TASK_KINDS, TaskSubmission
from .wrapper import ensure_summary, execute_task, format_status_snapshot, log_wrapper_event, run_command


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.load(repo_root=Path(__file__).resolve().parents[2])
    settings.ensure_wrapper_dirs()

    if args.command == "upstream":
        result = run_command(
            settings,
            [str(settings.resolved_hermes_bin), *args.args],
            action="upstream",
            capture_output=True,
        )
        _emit_result(result.stdout, result.stderr)
        return result.exit_code

    with Ledger(settings.ledger_db) as ledger:
        if args.command == "submit":
            submission = TaskSubmission(
                kind=args.kind,
                title=args.title,
                prompt=args.query,
                provider=args.provider or settings.default_provider,
                model=args.model or settings.default_model,
                repo_path=args.repo or "",
                require_approval=args.require_approval,
                created_by=args.created_by or os.environ.get("USER", "operator"),
                max_turns=args.max_turns,
            )
            task = ledger.create_task(submission)
            log_wrapper_event(
                settings,
                "task_submit",
                task_id=task["id"],
                kind=task["kind"],
                provider=task["provider"],
                model=task["model"],
                state=task["state"],
            )
            if args.run_now and task["approval_state"] not in {"not_required", APPROVAL_GRANTED}:
                print(f"{task['id']} queued in state {task['state']} and still needs approval.")
                return 0
            if args.run_now:
                outcome = execute_task(settings, ledger, task["id"])
                _print_execution(outcome, json_mode=args.json)
                return 0 if outcome.exit_code == 0 else 1
            _print_task(task, json_mode=args.json)
            return 0

        if args.command == "run":
            if args.approve:
                ledger.record_approval(
                    args.approve,
                    status=APPROVAL_GRANTED,
                    actor=args.actor or os.environ.get("USER", "operator"),
                    note=args.note or "Approved for execution via hermes-node run",
                )
                log_wrapper_event(
                    settings,
                    "approval_granted",
                    task_id=args.approve,
                    actor=args.actor or os.environ.get("USER", "operator"),
                )
                if not args.task_id:
                    args.task_id = args.approve
            return _run_loop(settings, ledger, args)

        if args.command == "status":
            payload = format_status_snapshot(ledger, limit=args.limit)
            if args.task_id:
                payload["task"] = ledger.get_task(args.task_id)
                payload["approvals"] = ledger.approvals_for_task(args.task_id)
                payload["events"] = ledger.events_for_task(args.task_id, limit=args.limit)
                summary = ledger.latest_summary(task_id=args.task_id)
                if summary is not None:
                    payload["summary"] = summary
            _print_status(payload, json_mode=args.json)
            return 0

        if args.command == "summarize":
            summary = ensure_summary(ledger, task_id=args.task_id or "", run_id=args.run_id or "")
            if args.json:
                print(json.dumps(summary, indent=2, sort_keys=True))
            else:
                print(summary["content"])
            return 0

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-node",
        description="Operator wrapper around upstream hermes-agent",
    )
    subparsers = parser.add_subparsers(dest="command")

    upstream = subparsers.add_parser("upstream", help="Pass through to upstream hermes")
    upstream.add_argument("args", nargs=argparse.REMAINDER)

    submit = subparsers.add_parser("submit", help="Queue a task in the local hermes-node ledger")
    submit.add_argument("--kind", required=True, choices=SUPPORTED_TASK_KINDS)
    submit.add_argument("--query", required=True)
    submit.add_argument("--title", default="")
    submit.add_argument("--provider")
    submit.add_argument("--model")
    submit.add_argument("--repo", help="Repository path for repo_read tasks")
    submit.add_argument("--max-turns", type=int)
    submit.add_argument("--created-by", default="")
    submit.add_argument("--require-approval", action="store_true")
    submit.add_argument("--run-now", action="store_true")
    submit.add_argument("--json", action="store_true")

    run_parser = subparsers.add_parser("run", help="Execute one queued task or watch the queue")
    run_parser.add_argument("--task-id", default="", help="Run a specific task")
    run_parser.add_argument("--watch", action="store_true", help="Keep polling for new runnable tasks")
    run_parser.add_argument("--limit", type=int, default=0, help="Maximum tasks to execute before exiting")
    run_parser.add_argument("--poll-interval", type=int, default=0, help="Seconds between queue polls")
    run_parser.add_argument("--approve", default="", help="Approve a waiting task before running it")
    run_parser.add_argument("--actor", default="", help="Approval actor when using --approve")
    run_parser.add_argument("--note", default="", help="Approval note when using --approve")
    run_parser.add_argument("--json", action="store_true")

    status_parser = subparsers.add_parser("status", help="Inspect queue, runs, approvals, and recent summaries")
    status_parser.add_argument("--task-id", default="")
    status_parser.add_argument("--limit", type=int, default=5)
    status_parser.add_argument("--json", action="store_true")

    summarize_parser = subparsers.add_parser("summarize", help="Show the latest stored summary for a task or run")
    summarize_parser.add_argument("--task-id", default="")
    summarize_parser.add_argument("--run-id", default="")
    summarize_parser.add_argument("--json", action="store_true")

    return parser


def _run_loop(settings: Settings, ledger: Ledger, args: argparse.Namespace) -> int:
    executed = 0
    poll_interval = args.poll_interval or settings.poll_interval_seconds
    while True:
        task_id = args.task_id
        if task_id:
            task = ledger.get_task(task_id)
            if task["state"] not in {"pending", "waiting_approval"}:
                message = f"Task {task_id} is in state {task['state']} and will not be re-run."
                if args.json:
                    print(json.dumps({"task_id": task_id, "message": message}, indent=2))
                else:
                    print(message)
                return 1
        else:
            task = ledger.next_runnable_task()
            if task is None:
                if not args.watch:
                    if args.json:
                        print(json.dumps({"message": "No runnable tasks."}, indent=2))
                    else:
                        print("No runnable tasks.")
                    return 0
                time.sleep(poll_interval)
                continue
            task_id = task["id"]

        outcome = execute_task(settings, ledger, task_id)
        _print_execution(outcome, json_mode=args.json)
        executed += 1
        args.task_id = ""
        if outcome.exit_code not in (0, None):
            return 1
        if args.limit and executed >= args.limit:
            return 0
        if not args.watch:
            return 0


def _print_task(task: dict[str, object], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(task, indent=2, sort_keys=True))
        return
    print(f"{task['id']} [{task['state']}]")
    print(f"kind: {task['kind']}")
    if task["provider"] or task["model"]:
        print(f"model: {task['provider'] or '-'} / {task['model'] or '-'}")
    if task["repo_path"]:
        print(f"repo: {task['repo_path']}")


def _print_execution(outcome: object, *, json_mode: bool) -> None:
    payload = {
        "task_id": outcome.task_id,
        "run_id": outcome.run_id,
        "state": outcome.state,
        "exit_code": outcome.exit_code,
        "summary": outcome.summary,
    }
    if json_mode:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"{outcome.task_id} -> {outcome.state}")
    if outcome.run_id:
        print(f"run: {outcome.run_id}")
    if outcome.exit_code is not None:
        print(f"exit_code: {outcome.exit_code}")
    print()
    print(outcome.summary)


def _print_status(payload: dict[str, object], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    counts = payload["counts"]
    print("counts:")
    for state in sorted(counts):
        print(f"  {state}: {counts[state]}")
    print()
    print("recent tasks:")
    for task in payload["recent_tasks"]:
        print(f"  {task['id']} [{task['state']}] {task['kind']} {task['provider'] or '-'} {task['model'] or '-'}")
    print()
    print("recent runs:")
    for run in payload["recent_runs"]:
        print(f"  {run['id']} [{run['state']}] task={run['task_id']} exit={run['exit_code']}")
    task = payload.get("task")
    if task:
        print()
        print(f"task detail: {task['id']} [{task['state']}]")
        for approval in payload.get("approvals", []):
            print(f"  approval {approval['status']} by {approval['actor']}: {approval['note']}")
        summary = payload.get("summary")
        if summary:
            print()
            print("latest summary:")
            print(summary["content"])


def _emit_result(stdout: str, stderr: str) -> None:
    if stdout:
        sys.stdout.write(stdout)
        if not stdout.endswith("\n"):
            sys.stdout.write("\n")
    if stderr:
        sys.stderr.write(stderr)
        if not stderr.endswith("\n"):
            sys.stderr.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
