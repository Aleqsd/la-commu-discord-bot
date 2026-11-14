from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Collection, Deque, Dict, Optional

import discord

# URLs processed per /jobbot post or preview request.
MAX_URLS_PER_REQUEST = 20
MAX_URLS_PER_REQUEST_TRUSTED = 50

HOUR_SECONDS = 3600.0
DAY_SECONDS = 86400.0


def is_trusted(
    user_id: int,
    permissions: Optional[discord.Permissions],
    trusted_user_ids: Collection[int],
) -> bool:
    """Trusted members bypass the rate limit and get the higher URL cap."""
    if user_id in trusted_user_ids:
        return True
    if permissions is None:
        return False
    return bool(permissions.administrator or permissions.manage_guild)


def max_urls_for(trusted: bool) -> int:
    return MAX_URLS_PER_REQUEST_TRUSTED if trusted else MAX_URLS_PER_REQUEST


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after: float = 0.0
    window: Optional[str] = None
    limit: Optional[int] = None
    # Time recorded for an allowed request (None when nothing was recorded).
    stamp: Optional[float] = None


class RequestRateLimiter:
    """Per-user sliding-window limiter kept in memory (it resets when the bot restarts).

    A limit of 0 or less disables that window.
    """

    def __init__(
        self,
        *,
        max_per_hour: int,
        max_per_day: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._windows = tuple(
            (name, seconds, limit)
            for name, seconds, limit in (
                ("hour", HOUR_SECONDS, max_per_hour),
                ("day", DAY_SECONDS, max_per_day),
            )
            if limit > 0
        )
        self._horizon = max((seconds for _, seconds, _ in self._windows), default=0.0)
        self._clock = clock
        self._history: Dict[int, Deque[float]] = {}

    def acquire(self, user_id: int) -> RateLimitDecision:
        """Record a request for ``user_id`` if it is within every window."""
        if not self._windows:
            return RateLimitDecision(allowed=True)

        now = self._clock()
        history = self._history.setdefault(user_id, deque())
        while history and history[0] <= now - self._horizon:
            history.popleft()

        blocked: Optional[RateLimitDecision] = None
        for name, seconds, limit in self._windows:
            recent = [stamp for stamp in history if stamp > now - seconds]
            if len(recent) < limit:
                continue
            # The request becomes possible once enough of the recent ones leave the window.
            retry_after = recent[len(recent) - limit] + seconds - now
            if blocked is None or retry_after > blocked.retry_after:
                blocked = RateLimitDecision(
                    allowed=False,
                    retry_after=retry_after,
                    window=name,
                    limit=limit,
                )
        if blocked is not None:
            return blocked

        history.append(now)
        return RateLimitDecision(allowed=True, stamp=now)

    def release(self, user_id: int, decision: RateLimitDecision) -> None:
        """Give back the slot recorded by ``decision`` (the request never ran)."""
        history = self._history.get(user_id)
        if decision.stamp is None or not history:
            return
        try:
            history.remove(decision.stamp)
        except ValueError:
            pass
