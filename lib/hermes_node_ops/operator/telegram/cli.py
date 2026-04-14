"""CLI entry point for the hermes-node operator Telegram bot."""

from __future__ import annotations

import argparse
from pathlib import Path

from ...config import Settings
from .bot import build_bot
from .client import TelegramClient


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.load(repo_root=Path(__file__).resolve().parents[4])
    settings.ensure_wrapper_dirs()

    if args.command == "run":
        return _handle_run(settings)
    if args.command == "check":
        return _handle_check(settings)

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-operator-telegram",
        description="Telegram ingress wedge for the hermes-node operator",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run", help="Run the long-polling Telegram bot loop")
    subparsers.add_parser("check", help="Verify token / bot identity via getMe")
    return parser


def _handle_run(settings: Settings) -> int:
    if not settings.telegram_bot_token:
        print(
            "HERMES_OPERATOR_TELEGRAM_TOKEN not set.\n"
            "Create a bot via @BotFather and put the token in .env."
        )
        return 1
    if not settings.telegram_allowed_chat_ids:
        print(
            "HERMES_OPERATOR_TELEGRAM_ALLOWED_CHAT_IDS is empty.\n"
            "The bot will refuse all updates until you allowlist at least one chat_id.\n"
            "Tip: message the bot once, then check the logs for your chat_id."
        )
    bot = build_bot(settings)
    print(
        f"hermes-operator-telegram starting "
        f"(allowlist={sorted(settings.telegram_allowed_chat_ids)}, "
        f"model={bot.backend.model_name})"
    )
    bot.run_forever()
    return 0


def _handle_check(settings: Settings) -> int:
    if not settings.telegram_bot_token:
        print("HERMES_OPERATOR_TELEGRAM_TOKEN not set.")
        return 1
    client = TelegramClient(token=settings.telegram_bot_token, timeout_seconds=10)
    try:
        me = client.get_me()
    except RuntimeError as exc:
        print(f"token check failed: {exc}")
        return 1
    print(f"bot: @{me.get('username','?')} id={me.get('id','?')} name={me.get('first_name','?')}")
    print(f"allowlist: {sorted(settings.telegram_allowed_chat_ids) or '(empty)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
