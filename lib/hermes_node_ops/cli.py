from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .wrapper import (
    api_health,
    api_submit,
    build_service_command,
    build_submit_command,
    run_command,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.load(repo_root=Path(__file__).resolve().parents[2])
    settings.ensure_wrapper_dirs()

    if args.command == "upstream":
        result = run_command(settings, [str(settings.resolved_hermes_bin), *args.args], action="upstream", capture_output=True)
        _emit_result(result.stdout, result.stderr)
        return result.exit_code

    if args.command == "smoke":
        exit_code = 0
        for label, command in (
            ("version", [str(settings.resolved_hermes_bin), "version"]),
            ("status", [str(settings.resolved_hermes_bin), "status"]),
            ("doctor", [str(settings.resolved_hermes_bin), "doctor"]),
        ):
            result = run_command(settings, command, action=f"smoke_{label}", capture_output=True)
            print(f"== {label} ==")
            _emit_result(result.stdout, result.stderr)
            exit_code = exit_code or result.exit_code
        return exit_code

    if args.command == "service":
        command = build_service_command(settings, args.action, system=args.system)
        result = run_command(settings, command, action=f"service_{args.action}", capture_output=False)
        return result.exit_code

    if args.command == "submit":
        command = build_submit_command(
            settings,
            query=args.query,
            provider=args.provider or "",
            model=args.model or "",
            quiet=not args.no_quiet,
            max_turns=args.max_turns,
            worktree=args.worktree,
            yolo=args.yolo,
        )
        result = run_command(settings, command, action="submit", capture_output=True)
        _emit_result(result.stdout, result.stderr)
        return result.exit_code

    if args.command == "api-health":
        status, payload = api_health(settings)
        print(payload)
        return 0 if status == 200 else 1

    if args.command == "api-submit":
        status, payload = api_submit(
            settings,
            input_text=args.input,
            instructions=args.instructions,
            conversation=args.conversation,
            store=not args.no_store,
        )
        if args.json:
            try:
                print(json.dumps(json.loads(payload), indent=2))
            except json.JSONDecodeError:
                print(payload)
        else:
            print(payload)
        return 0 if 200 <= status < 300 else 1

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-node", description="Thin wrapper around upstream hermes-agent")
    subparsers = parser.add_subparsers(dest="command")

    upstream = subparsers.add_parser("upstream", help="Pass through to upstream hermes")
    upstream.add_argument("args", nargs=argparse.REMAINDER)

    subparsers.add_parser("smoke", help="Run upstream version/status/doctor")

    service = subparsers.add_parser("service", help="Manage the upstream gateway service")
    service.add_argument("action", choices=["install", "start", "stop", "restart", "status", "uninstall"])
    service.add_argument("--system", action="store_true", help="Target the upstream system service")

    submit = subparsers.add_parser("submit", help="Submit a one-shot query through upstream hermes chat")
    submit.add_argument("--query", required=True)
    submit.add_argument("--provider")
    submit.add_argument("--model")
    submit.add_argument("--max-turns", type=int)
    submit.add_argument("--worktree", action="store_true")
    submit.add_argument("--yolo", action="store_true")
    submit.add_argument("--no-quiet", action="store_true", help="Do not pass -Q to hermes chat")

    subparsers.add_parser("api-health", help="Check the upstream local API health endpoint")

    api_submit_parser = subparsers.add_parser("api-submit", help="Submit work to the upstream local API server")
    api_submit_parser.add_argument("--input", required=True)
    api_submit_parser.add_argument("--instructions", default="")
    api_submit_parser.add_argument("--conversation", default="")
    api_submit_parser.add_argument("--no-store", action="store_true")
    api_submit_parser.add_argument("--json", action="store_true")

    return parser


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
