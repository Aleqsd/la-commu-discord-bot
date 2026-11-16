from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

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
MAX_LINK_LABEL_CHARS = 80
# Per-site blocks that hold other content than the page's own (CSS selectors), keyed by
# the site's domain; they apply to its subdomains too. LinkedIn's public post page appends
# "More Relevant Posts" (other people's posts, often other job offers) after the post.
SITE_NOISE_SELECTORS = {
    "linkedin.com": (".related-posts",),
}


class FetchError(Exception):
    """Raised when a page cannot be fetched."""


class UnsafeURLError(FetchError):
    """Raised when a URL uses a disallowed scheme or points to a non-public address."""


@dataclass(frozen=True, slots=True)
class PageContent:
    text: str
    # Absolute http(s) targets of the page's <a href> links, without fragments,
    # deduplicated in page order.
    links: Tuple[str, ...] = field(default_factory=tuple)
    # Visible text (else title / aria-label) of the first anchor naming each link, so the
    # model can tell which link belongs to which job. Links without any text are absent.
    link_labels: Mapping[str, str] = field(default_factory=dict)


async def _resolve_host(host: str, port: int) -> List[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _ensure_global_address(address: str, host: str) -> None:
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError as exc:
        raise UnsafeURLError(f"host '{host}' uses unexpected address {address!r}") from exc
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_global:
        raise UnsafeURLError(f"host '{host}' resolves to non-public address {ip}")


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
        _ensure_global_address(address, host)


def ensure_public_peer(response: httpx.Response) -> None:
    """Check the address httpx actually connected to (DNS may have changed since the lookup).

    httpcore exposes it as ``network_stream.get_extra_info("server_addr")``, a (host, port, ...)
    tuple. A missing address is refused as well.
    """
    host = response.request.url.host
    stream = response.extensions.get("network_stream")
    server_addr = stream.get_extra_info("server_addr") if stream is not None else None
    if not server_addr:
        raise UnsafeURLError(f"could not verify the address connected to for '{host}'")
    address = server_addr[0] if isinstance(server_addr, (tuple, list)) else server_addr
    _ensure_global_address(address, host)


def _media_type(content_type: Optional[str]) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


async def _download_page(url: str, *, timeout: float, max_bytes: int) -> Tuple[bytes, str, str, str]:
    """Fetch ``url`` following at most MAX_REDIRECTS redirects, validating every hop.

    Returns the body, media type, charset and final URL.
    """
    current = httpx.URL(url)
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await ensure_public_url(current)
            async with client.stream("GET", current) as response:
                # Raising here leaves the block, which closes the stream unread.
                ensure_public_peer(response)
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
                return bytes(body), media_type, response.charset_encoding or "utf-8", str(current)
    raise FetchError(f"more than {MAX_REDIRECTS} redirects")


def _decode(body: bytes, encoding: str) -> str:
    try:
        return body.decode(encoding, errors="replace")
    except (LookupError, UnicodeError):
        # Unknown charsets, and codecs such as "idna" that reject errors="replace".
        return body.decode("utf-8", errors="replace")


def _absolute_link(href: object, base_url: str) -> Optional[str]:
    if not isinstance(href, str) or not href.strip():
        return None
    try:
        absolute = urldefrag(urljoin(base_url, href.strip())).url
    except ValueError:
        return None
    if not absolute.lower().startswith(("http://", "https://")):
        return None
    return absolute


def _anchor_label(anchor: Tag) -> str:
    text = anchor.get_text(" ", strip=True)
    if not text:
        for attribute in ("title", "aria-label"):
            value = anchor.get(attribute)
            if isinstance(value, str) and value.strip():
                text = value
                break
    return " ".join(text.split())[:MAX_LINK_LABEL_CHARS]


def _site_noise_selectors(url: str) -> Tuple[str, ...]:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ()
    for domain, selectors in SITE_NOISE_SELECTORS.items():
        if host == domain or host.endswith("." + domain):
            return selectors
    return ()


def _extract_page(body: bytes, media_type: str, encoding: str, base_url: str) -> PageContent:
    markup = _decode(body, encoding)
    links: Tuple[str, ...] = ()
    labels: Dict[str, str] = {}
    if media_type == "text/plain":
        text = markup
    else:
        soup = BeautifulSoup(markup, "html.parser")
        for tag in soup(DROPPED_TAGS):
            tag.decompose()
        for tag in soup(CHROME_TAGS):
            if not tag.decomposed and tag.find_parent(CONTENT_CONTAINERS) is None:
                tag.decompose()
        for selector in _site_noise_selectors(base_url):
            for tag in soup.select(selector):
                if not tag.decomposed:
                    tag.decompose()
        ordered: Dict[str, None] = {}
        for anchor in soup.find_all("a"):
            link = _absolute_link(anchor.get("href"), base_url)
            if not link:
                continue
            ordered.setdefault(link, None)
            if link not in labels:
                label = _anchor_label(anchor)
                if label:
                    labels[link] = label
        links = tuple(ordered)
        text = soup.get_text(separator="\n")
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return PageContent(text=cleaned, links=links, link_labels=labels)


async def fetch_page_text(url: str, *, timeout: float, max_bytes: int) -> Optional[PageContent]:
    logger.info("🕸️ Fetching page: %s", url)
    try:
        body, media_type, encoding, final_url = await asyncio.wait_for(
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

    page = await run_blocking(_extract_page, body, media_type, encoding, final_url)
    if not page.text:
        logger.warning("⚠️ Empty text after parsing %s", url)
        return None
    logger.info("✅ Extracted text and %d link(s) from %s", len(page.links), url)
    return page
