from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import Sequence

from bot.client import LaCommuDiscordBot
from bot.config import load_config
from bot.health import HealthServer
from bot.openai_client import OpenAIJobParser
from bot.retry import RetryManager

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DEFAULT_LOG_FILE = Path("job-caster.log")
logger = logging.getLogger(__name__)


def configure_logging(log_file: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    destination = DEFAULT_LOG_FILE if log_file is None else log_file
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(destination))

    logging.basicConfig(
        level=LOG_LEVEL,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch LaCommu Discord bot")
    parser.add_argument(
        "--log-file",
        type=Path,
        help=f"Path to a log file (default: {DEFAULT_LOG_FILE}).",
    )
    return parser.parse_args(argv)


configure_logging(DEFAULT_LOG_FILE)


async def run_bot() -> None:
    config = load_config()
    parser = OpenAIJobParser(config.openai)
    retry_manager = RetryManager(Path("data/pending_requests.json"))
    bot = LaCommuDiscordBot(config, parser, retry_manager)
    health_server = HealthServer()

    await health_server.start()
    try:
        await bot.start(config.discord_token)
    except Exception as exc:  # noqa: BLE001
        logger.exception("🛑 Bot stopped unexpectedly: %s", exc)
        raise
    finally:
        await health_server.stop()


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    configure_logging(args.log_file)
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
