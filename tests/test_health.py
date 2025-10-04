from __future__ import annotations

import asyncio

import pytest

from bot.health import HealthServer


def test_health_server_registers_expected_routes() -> None:
    async def runner() -> None:
        server = HealthServer(port=0)
        try:
            await server.start()
            assert server._runner is not None  # noqa: SLF001
            routes = {
                route.resource.get_info().get("path")
                for route in server._runner.app.router.routes()  # noqa: SLF001
            }
            assert "/health" in routes
            assert "/" in routes
            assert "/healthz" in routes
        finally:
            await server.stop()

    asyncio.run(runner())


@pytest.mark.parametrize(
    "port_value",
    ["not-an-int", "", "   ", "-3"],
)
def test_health_server_handles_invalid_port_env(monkeypatch: pytest.MonkeyPatch, port_value: str) -> None:
    monkeypatch.setenv("PORT", port_value)
    server = HealthServer()
    assert server._port == HealthServer.DEFAULT_PORT  # noqa: SLF001


def test_health_server_prefers_explicit_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "65535")
    server = HealthServer(port=1234)
    assert server._port == 1234  # noqa: SLF001
