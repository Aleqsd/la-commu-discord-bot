from __future__ import annotations

import asyncio
import base64
import re
from typing import Iterable, List

URL_REGEX = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
IMAGE_REF_REGEX = re.compile(r"image\s*:\s*(https?://[^\s<>]+)", re.IGNORECASE)

TEAM_ALIASES = {
    "art": {
        "art",
        "aart",
        "visual_art",
        "artistic",
        "concept_art",
    },
    "game_design": {
        "game_design",
        "design",
        "gameplay_design",
        "level_design",
        "systems_design",
    },
    "dev": {
        "dev",
        "development",
        "programming",
        "engineering",
        "code",
        "software",
    },
    "others": {
        "others",
        "qa",
        "production",
        "producer",
        "biz",
        "marketing",
        "community",
        "support",
    },
}


def extract_urls(text: str) -> List[str]:
    return list(dict.fromkeys(URL_REGEX.findall(text or "")))


def extract_image_urls(text: str) -> List[str]:
    return list(dict.fromkeys(IMAGE_REF_REGEX.findall(text or "")))


def sanitize_team(team: str) -> str:
    if not team:
        return "others"
    normalized = team.lower().replace(" ", "_")
    for canonical, variants in TEAM_ALIASES.items():
        if normalized == canonical or normalized in variants:
            return canonical
    return "others"


async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


def chunk_text(text: str, size: int) -> Iterable[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]


def to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
