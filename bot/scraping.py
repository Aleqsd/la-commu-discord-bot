from __future__ import annotations

import logging
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "la-commu-discord-bot/1.0 (+https://github.com/la-commu)"


async def fetch_page_text(url: str, *, timeout: float, max_bytes: int) -> Optional[str]:
    logger.info("🕸️ Fetching page: %s", url)
    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ Failed to fetch %s: %s", url, exc)
        return None

    content = response.text
    if len(content) > max_bytes:
        logger.info("✂️ Trimming page content for %s to %s bytes", url, max_bytes)
        content = content[:max_bytes]

    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "img"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    if not text:
        logger.warning("⚠️ Empty text after parsing %s", url)
        return None
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
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
