from __future__ import annotations

import logging
import os
from typing import Optional

from aiohttp import web

logger = logging.getLogger(__name__)


class HealthServer:
    def __init__(self, host: str = "0.0.0.0", port: Optional[int] = None) -> None:
        self._host = host
        self._port = port or int(os.getenv("PORT", "8080"))
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.BaseSite] = None

    async def start(self) -> None:
        if self._runner:
            return
        app = web.Application()
        app.router.add_get("/", self._handle_health)
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
