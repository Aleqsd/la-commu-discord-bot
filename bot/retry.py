from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class PendingRequest:
    request_id: int
    guild_id: int
    user_id: int
    reference: str
    created_at: float
    attempts: int = 0
    last_error: str | None = None


class RetryManager:
    def __init__(self, storage_path: Path, *, max_attempts: int = 3) -> None:
        self._storage_path = storage_path
        self._max_attempts = max_attempts
        self._lock = asyncio.Lock()
        storage_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    async def start_request(
        self,
        *,
        request_id: int,
        guild_id: int,
        user_id: int,
        reference: str,
    ) -> None:
        async with self._lock:
            entries = self._load()
            for entry in entries:
                if entry.request_id == request_id:
                    entry.reference = reference
                    entry.guild_id = guild_id
                    entry.user_id = user_id
                    break
            else:
                entries.append(
                    PendingRequest(
                        request_id=request_id,
                        guild_id=guild_id,
                        user_id=user_id,
                        reference=reference,
                        created_at=time.time(),
                    )
                )
            self._save(entries)

    async def complete_request(self, request_id: int) -> None:
        async with self._lock:
            entries = self._load()
            new_entries = [entry for entry in entries if entry.request_id != request_id]
            if len(new_entries) != len(entries):
                logger.debug("✅ Cleared pending request %s", request_id)
                self._save(new_entries)

    async def fail_request(self, request_id: int, error: str) -> None:
        async with self._lock:
            entries = self._load()
            updated = False
            for entry in entries:
                if entry.request_id == request_id:
                    entry.attempts += 1
                    entry.last_error = error[:500]
                    updated = True
                    logger.warning(
                        "⚠️ Pending request %s failed (attempt %s/%s): %s",
                        request_id,
                        entry.attempts,
                        self._max_attempts,
                        entry.last_error,
                    )
                    break
            if not updated:
                entries.append(
                    PendingRequest(
                        request_id=request_id,
                        guild_id=0,
                        user_id=0,
                        reference="",
                        created_at=time.time(),
                        attempts=1,
                        last_error=error[:500],
                    )
                )
            self._save(entries)

    async def list_pending_requests(self) -> List[PendingRequest]:
        async with self._lock:
            return self._load()

    def _load(self) -> List[PendingRequest]:
        if not self._storage_path.exists():
            return []
        try:
            raw = json.loads(self._storage_path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("⚠️ Could not read retry storage. Resetting %s", self._storage_path)
            return []
        entries: List[PendingRequest] = []
        for item in raw:
            try:
                entries.append(PendingRequest(**item))
            except TypeError:
                logger.debug("Skipping malformed retry entry: %s", item)
        return entries

    def _save(self, entries: List[PendingRequest]) -> None:
        data = [asdict(entry) for entry in entries]
        self._storage_path.write_text(json.dumps(data, indent=2))
