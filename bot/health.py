from __future__ import annotations

import logging
import os
from typing import Optional

from aiohttp import web

logger = logging.getLogger(__name__)


class HealthServer:
    DEFAULT_PORT = 8080

    def __init__(self, host: str = "0.0.0.0", port: Optional[int] = None) -> None:
        self._host = host
        env_port_str = os.getenv("PORT")
        env_port = self.DEFAULT_PORT
        if env_port_str and env_port_str.strip():
            try:
                candidate = int(env_port_str)
            except ValueError:
                logger.warning(
                    "⚠️ Invalid PORT value '%s'; falling back to %s.",
                    env_port_str,
                    self.DEFAULT_PORT,
                )
            else:
                if candidate <= 0:
                    logger.warning(
                        "⚠️ Non-positive PORT value '%s'; falling back to %s.",
                        env_port_str,
                        self.DEFAULT_PORT,
                    )
                else:
                    env_port = candidate
        self._port = env_port if port is None else port
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.BaseSite] = None

    async def start(self) -> None:
        if self._runner:
            return
        app = web.Application()
        app.router.add_get("/", self._handle_health)
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/healthz", self._handle_health)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        logger.info("🌐 Health server listening on %s:%s", self._host, self._port)

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
            self._site = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        logger.info("🛑 Health server stopped")

    async def _handle_health(self, request: web.Request) -> web.Response:  # noqa: D401
        return web.json_response({"status": "ok"})
