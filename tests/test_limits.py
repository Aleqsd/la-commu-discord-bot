import discord
import pytest

from bot.limits import (
    MAX_URLS_PER_REQUEST,
    MAX_URLS_PER_REQUEST_TRUSTED,
    RequestRateLimiter,
    is_trusted,
    max_urls_for,
)


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_hourly_limit_blocks_then_reopens_when_window_slides():
    clock = FakeClock()
    limiter = RequestRateLimiter(max_per_hour=3, max_per_day=100, clock=clock)

    for _ in range(3):
        assert limiter.acquire(1).allowed
        clock.advance(60)

    decision = limiter.acquire(1)
    assert not decision.allowed
    assert decision.window == "hour"
    assert decision.limit == 3
    # First request was at t=1000, now is t=1180: it leaves the window at t=4600.
    assert decision.retry_after == pytest.approx(3600 - 180)

    # Blocked attempts are not counted.
    clock.advance(decision.retry_after - 1)
    assert not limiter.acquire(1).allowed
    clock.advance(1)
    assert limiter.acquire(1).allowed
    assert not limiter.acquire(1).allowed


def test_daily_limit_applies_across_hours():
    clock = FakeClock()
    limiter = RequestRateLimiter(max_per_hour=10, max_per_day=4, clock=clock)

    for _ in range(4):
        assert limiter.acquire(7).allowed
        clock.advance(2 * 3600)

    decision = limiter.acquire(7)
    assert not decision.allowed
    assert decision.window == "day"
    assert decision.limit == 4
    assert decision.retry_after == pytest.approx(86400 - 8 * 3600)

    clock.advance(decision.retry_after)
    assert limiter.acquire(7).allowed


def test_window_expiry_and_users_are_independent():
    clock = FakeClock()
    limiter = RequestRateLimiter(max_per_hour=1, max_per_day=2, clock=clock)

    assert limiter.acquire(1).allowed
    assert not limiter.acquire(1).allowed
    assert limiter.acquire(2).allowed  # another user has their own budget

    clock.advance(3600)
    assert limiter.acquire(1).allowed
    clock.advance(3600)
    day_blocked = limiter.acquire(1)
    assert not day_blocked.allowed and day_blocked.window == "day"

    clock.advance(86400)
    assert limiter.acquire(1).allowed


def test_zero_disables_a_window():
    clock = FakeClock()
    limiter = RequestRateLimiter(max_per_hour=0, max_per_day=0, clock=clock)
    assert all(limiter.acquire(1).allowed for _ in range(500))


def test_trusted_members():
    assert is_trusted(5, discord.Permissions(manage_guild=True), frozenset())
    assert is_trusted(5, discord.Permissions(administrator=True), frozenset())
    assert is_trusted(5, discord.Permissions.none(), frozenset({5}))
    assert not is_trusted(5, discord.Permissions(send_messages=True), frozenset({6}))
    assert not is_trusted(5, None, frozenset())


def test_url_caps():
    assert max_urls_for(False) == MAX_URLS_PER_REQUEST == 20
    assert max_urls_for(True) == MAX_URLS_PER_REQUEST_TRUSTED == 50


def test_release_gives_back_exactly_the_recorded_slot():
    clock = FakeClock()
    limiter = RequestRateLimiter(max_per_hour=2, max_per_day=100, clock=clock)

    first = limiter.acquire(1)
    clock.advance(10)
    second = limiter.acquire(1)
    assert not limiter.acquire(1).allowed

    limiter.release(1, first)
    assert limiter.acquire(1).allowed
    assert not limiter.acquire(1).allowed

    # Releasing a blocked decision, or the same slot twice, changes nothing.
    limiter.release(1, limiter.acquire(1))
    limiter.release(1, first)
    assert not limiter.acquire(1).allowed
    assert second.stamp is not None
