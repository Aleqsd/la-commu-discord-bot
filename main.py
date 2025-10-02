from __future__ import annotations

import asyncio
import logging
import os

from bot.client import LaCommuDiscordBot
from bot.config import load_config
from bot.health import HealthServer
from bot.openai_client import OpenAIJobParser

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def run_bot() -> None:
    config = load_config()
    parser = OpenAIJobParser(config.openai)
    bot = LaCommuDiscordBot(config, parser)
    health_server = HealthServer()

    await health_server.start()
    try:
        await bot.start(config.discord_token)
    except Exception as exc:  # noqa: BLE001
        logger.exception("🛑 Bot stopped unexpectedly: %s", exc)
        raise
    finally:
        await health_server.stop()


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
