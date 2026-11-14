from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Set
from urllib.parse import parse_qsl, urlencode, urlsplit

from .utils import run_blocking

if TYPE_CHECKING:
    from .models import JobPosting

TRACKING_PARAMS = frozenset({"trackingid", "refid", "fbclid", "gclid"})
TRACKING_PARAM_PREFIXES = ("utm_",)


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PARAM_PREFIXES)


def legacy_normalize_url(url: Optional[str]) -> str:
    """Normalisation used by the original history format (kept to read old entries)."""
    return (url or "").strip().lower().split("#", 1)[0].rstrip("/")


def normalize_url(url: Optional[str]) -> str:
    """Canonical form of a URL for duplicate detection.

    Lowercases scheme and host, treats http and https alike, strips ``www.``, drops the
    fragment, credentials, default ports and tracking parameters, sorts the remaining
    query parameters and removes the trailing slash.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return legacy_normalize_url(raw)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return legacy_normalize_url(raw)
    if scheme == "http":
        scheme = "https"
    if host.startswith("www."):
        host = host[4:]
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = parts.path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_tracking_param(key)
        )
    )
    return f"{scheme}://{netloc}{path}" + (f"?{query}" if query else "")


def _normalize_text(value: Optional[str]) -> str:
    return " ".join((value or "").split()).casefold()


def build_job_key(job: "JobPosting") -> str:
    """Key written to history.

    A job with its own URL is keyed by that URL. A job that only carries its source
    (page or image listing several offers) is keyed by source + company + title.
    """
    job_url = normalize_url(job.job_url)
    source_url = normalize_url(job.source_url)
    if job_url and job_url != source_url:
        return job_url
    base = source_url or job_url
    company = _normalize_text(job.company_name)
    title = _normalize_text(job.job_title)
    if not (base or company or title):
        return ""
    return f"{base}|{company}|{title}"


def build_legacy_key(job: "JobPosting") -> str:
    """Key produced by the original algorithm, matched against existing history lines."""
    url = (job.job_url or "").strip().lower()
    if url:
        return url.split("#", 1)[0].rstrip("/")

    company = (job.company_name or "").strip().lower()
    title = (job.job_title or "").strip().lower()
    team = (job.team or "").strip().lower()
    if not company and not title:
        return ""
    return "|".join(filter(None, (company, title, team)))


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
        keys = {key for key in (build_job_key(job), build_legacy_key(job)) if key}
        if not keys:
            return False
        async with self._lock:
            return any(key in self._seen for key in keys)

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
        return build_job_key(job)
