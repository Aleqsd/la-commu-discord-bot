from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Set

from .utils import run_blocking

if TYPE_CHECKING:
    from .models import JobPosting


class PostHistory:
    """Stores identifiers of previously posted jobs to avoid duplicates."""

    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path
        self._seen: Set[str] = set()
        self._lock = asyncio.Lock()
        self._loaded = False

    async def load(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            entries = await run_blocking(self._read_entries)
            self._seen = entries
            self._loaded = True

    async def is_posted(self, job: "JobPosting") -> bool:
        await self.load()
        key = self._build_key(job)
        if not key:
            return False
        async with self._lock:
            return key in self._seen

    async def mark_posted(self, job: "JobPosting") -> None:
        await self.load()
        key = self._build_key(job)
        if not key:
            return
        async with self._lock:
            if key in self._seen:
                return
            self._seen.add(key)
        await run_blocking(self._append_entry, key)

    def _read_entries(self) -> Set[str]:
        if not self.storage_path.exists():
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            return set()
        with self.storage_path.open("r", encoding="utf-8") as handle:
            return {line.strip() for line in handle if line.strip()}

    def _append_entry(self, key: str) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self.storage_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{key}\n")

    def _build_key(self, job: "JobPosting") -> str:
        url = (job.job_url or "").strip().lower()
        if url:
            sanitized = url.split("#", 1)[0].rstrip("/")
            return sanitized

        company = (job.company_name or "").strip().lower()
        title = (job.job_title or "").strip().lower()
        team = (job.team or "").strip().lower()
        if not company and not title:
            return ""
        return "|".join(filter(None, (company, title, team)))
