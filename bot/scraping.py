from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import socket
from typing import List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from .utils import run_blocking

logger = logging.getLogger(__name__)

USER_AGENT = "la-commu-discord-bot/1.0 (+https://github.com/la-commu)"

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PAGE_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
MAX_REDIRECTS = 5
# Removed wherever they appear.
DROPPED_TAGS = ["script", "style", "noscript", "svg", "img", "nav"]
# Removed when they are page chrome, kept when they belong to an article/main/section
# (where a <header> often holds the job title).
CHROME_TAGS = ["header", "footer"]
CONTENT_CONTAINERS = ["article", "main", "section"]


class FetchError(Exception):
    """Raised when a page cannot be fetched."""


class UnsafeURLError(FetchError):
    """Raised when a URL uses a disallowed scheme or points to a non-public address."""


async def _resolve_host(host: str, port: int) -> List[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def ensure_public_url(url: httpx.URL) -> None:
    """Reject URLs that are not http(s) or whose host resolves to a non-global address."""
    if url.scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"scheme '{url.scheme or 'none'}' is not allowed")
    host = url.raw_host.decode("ascii", errors="replace")
    if not host:
        raise UnsafeURLError("URL has no host")
    port = url.port or (443 if url.scheme == "https" else 80)
    try:
        addresses = await _resolve_host(host, port)
    except OSError as exc:
        raise UnsafeURLError(f"cannot resolve host '{host}': {exc}") from exc
    if not addresses:
        raise UnsafeURLError(f"host '{host}' did not resolve to any address")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise UnsafeURLError(f"host '{host}' resolved to unexpected address {address!r}") from exc
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise UnsafeURLError(f"host '{host}' resolves to non-public address {ip}")


def _media_type(content_type: Optional[str]) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


async def _download_page(url: str, *, timeout: float, max_bytes: int) -> Tuple[bytes, str, str]:
    """Fetch ``url`` following at most MAX_REDIRECTS redirects, validating every hop."""
    current = httpx.URL(url)
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await ensure_public_url(current)
            async with client.stream("GET", current) as response:
                if response.is_redirect:
                    current = current.join(response.headers["location"])
                    logger.info("↪️ Redirected to %s", current)
                    continue
                response.raise_for_status()

                media_type = _media_type(response.headers.get("content-type"))
                if media_type not in ALLOWED_PAGE_CONTENT_TYPES:
                    raise FetchError(f"unsupported content type '{media_type or 'missing'}'")
                declared_length = response.headers.get("content-length")
                if declared_length and declared_length.isdigit() and int(declared_length) > max_bytes:
                    raise FetchError(f"page is {declared_length} bytes (limit {max_bytes})")

                body = bytearray()
                async with contextlib.aclosing(response.aiter_bytes()) as chunks:
                    async for chunk in chunks:
                        body.extend(chunk)
                        if len(body) >= max_bytes:
                            del body[max_bytes:]
                            logger.info("✂️ Stopped reading %s at %s bytes", url, max_bytes)
                            break
                return bytes(body), media_type, response.charset_encoding or "utf-8"
    raise FetchError(f"more than {MAX_REDIRECTS} redirects")


def _decode(body: bytes, encoding: str) -> str:
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _extract_text(body: bytes, media_type: str, encoding: str) -> str:
    markup = _decode(body, encoding)
    if media_type == "text/plain":
        text = markup
    else:
        soup = BeautifulSoup(markup, "html.parser")
        for tag in soup(DROPPED_TAGS):
            tag.decompose()
        for tag in soup(CHROME_TAGS):
            if not tag.decomposed and tag.find_parent(CONTENT_CONTAINERS) is None:
                tag.decompose()
        text = soup.get_text(separator="\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


async def fetch_page_text(url: str, *, timeout: float, max_bytes: int) -> Optional[str]:
    logger.info("🕸️ Fetching page: %s", url)
    try:
        body, media_type, encoding = await asyncio.wait_for(
            _download_page(url, timeout=timeout, max_bytes=max_bytes),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("⚠️ Timed out fetching %s after %ss", url, timeout)
        return None
    except UnsafeURLError as exc:
        logger.warning("🚫 Refused to fetch %s: %s", url, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ Failed to fetch %s: %s", url, exc)
        return None

    cleaned = await run_blocking(_extract_text, body, media_type, encoding)
    if not cleaned:
        logger.warning("⚠️ Empty text after parsing %s", url)
        return None
    logger.info("✅ Extracted text from %s", url)
    return cleaned


async def fetch_image_bytes(url: str, *, timeout: float, max_bytes: int) -> Optional[bytes]:
    logger.info("🖼️ Fetching image: %s", url)
    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ Failed to fetch image %s: %s", url, exc)
        return None

    content = response.content
    if len(content) > max_bytes:
        logger.warning("⚠️ Image %s is larger than %s bytes", url, max_bytes)
        return None
    logger.info("✅ Retrieved image bytes for %s", url)
    return content
