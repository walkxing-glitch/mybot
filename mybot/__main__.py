"""MyBot 入口点：python -m mybot cli | telegram"""

from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    # Dispatch to palace CLI: `python -m mybot memory <subcommand>`
    if len(sys.argv) > 1 and sys.argv[1] == "memory":
        from mybot.palace.cli import main as palace_cli_main
        sys.exit(palace_cli_main(sys.argv[2:]))

    parser = argparse.ArgumentParser(
        prog="mybot",
        description="MyBot — 个人 AI Agent（本体论大脑 + 记忆引擎）",
    )
    subparsers = parser.add_subparsers(dest="gateway", required=True)

    cli_parser = subparsers.add_parser("cli", help="启动交互式 CLI")
    cli_parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径（默认 config.yaml）",
    )
    cli_parser.add_argument(
        "--session",
        default="cli-default",
        help="会话 ID（默认 cli-default）",
    )

    tg_parser = subparsers.add_parser("telegram", help="启动 Telegram bot")
    tg_parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径（默认 config.yaml）",
    )

    args = parser.parse_args()

    if args.gateway == "cli":
        try:
            from mybot.gateway.cli import run_cli_from_config  # type: ignore[attr-defined]
        except ImportError:
            print(
                "[mybot] CLI gateway 尚未实现（mybot/gateway/cli.py 未找到）。",
                file=sys.stderr,
            )
            sys.exit(1)
        asyncio.run(
            run_cli_from_config(config_path=args.config, session_id=args.session)
        )

    elif args.gateway == "telegram":
        try:
            from mybot.gateway.telegram import run_telegram_from_config  # type: ignore[attr-defined]
        except ImportError:
            print(
                "[mybot] Telegram gateway 尚未实现（mybot/gateway/telegram.py 未找到）。",
                file=sys.stderr,
            )
            sys.exit(1)
        asyncio.run(run_telegram_from_config(config_path=args.config))


if __name__ == "__main__":
    main()
