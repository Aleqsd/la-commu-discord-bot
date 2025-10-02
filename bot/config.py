from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict

from dotenv import load_dotenv

load_dotenv()


@dataclass(slots=True)
class ChannelConfig:
    team_channels: Dict[str, int] = field(
        default_factory=lambda: {
            "art": 0,
            "game_design": 0,
            "dev": 0,
            "others": 0,
        }
    )


@dataclass(slots=True)
class OpenAIConfig:
    api_key: str
    model: str = "gpt-5.1-mini"
    temperature: float = 0.1
    image_model: str | None = None


@dataclass(slots=True)
class BotConfig:
    discord_token: str
    openai: OpenAIConfig
    channels: ChannelConfig = field(default_factory=ChannelConfig)
    max_scrape_bytes: int = 600_000
    max_image_bytes: int = 5_000_000
    request_timeout: float = 30.0
    response_timeout: float = 60.0


def _parse_team_channel_ids(raw: str | None) -> Dict[str, int]:
    base = ChannelConfig().team_channels.copy()
    if not raw:
        return base

    for item in raw.split(","):
        if not item.strip():
            continue
        team, _, channel_id_str = item.partition(":")
        team_key = team.strip().lower().replace(" ", "_")
        channel_id_str = channel_id_str.strip()
        if not team_key or not channel_id_str:
            continue
        try:
            channel_id = int(channel_id_str)
        except ValueError as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Invalid channel ID '{channel_id_str}' for team '{team_key}'."
            ) from exc
        if channel_id <= 0:
            raise RuntimeError(
                f"Channel ID for team '{team_key}' must be a positive integer."
            )
        base[team_key] = channel_id
    return base


def load_config() -> BotConfig:
    discord_token = os.getenv("DISCORD_BOT_TOKEN")
    openai_key = os.getenv("OPENAI_API_KEY")
    if not discord_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required")
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    team_channels = _parse_team_channel_ids(os.getenv("JOB_TEAM_CHANNEL_IDS"))
    missing_ids = [team for team, channel_id in team_channels.items() if channel_id <= 0]
    if missing_ids:
        raise RuntimeError(
            "Provide numeric IDs for all team channels via JOB_TEAM_CHANNEL_IDS. Missing: "
            + ", ".join(missing_ids)
        )

    channels = ChannelConfig(team_channels=team_channels)
    openai_cfg = OpenAIConfig(
        api_key=openai_key,
        model=os.getenv("OPENAI_MODEL", OpenAIConfig.__dataclass_fields__["model"].default),
        temperature=float(os.getenv("OPENAI_TEMPERATURE", OpenAIConfig.__dataclass_fields__["temperature"].default)),
        image_model=os.getenv("OPENAI_IMAGE_MODEL"),
    )
    return BotConfig(
        discord_token=discord_token,
        openai=openai_cfg,
        channels=channels,
        max_scrape_bytes=int(os.getenv("MAX_SCRAPE_BYTES", BotConfig.__dataclass_fields__["max_scrape_bytes"].default)),
        max_image_bytes=int(os.getenv("MAX_IMAGE_BYTES", BotConfig.__dataclass_fields__["max_image_bytes"].default)),
        request_timeout=float(os.getenv("REQUEST_TIMEOUT", BotConfig.__dataclass_fields__["request_timeout"].default)),
        response_timeout=float(os.getenv("RESPONSE_TIMEOUT", BotConfig.__dataclass_fields__["response_timeout"].default)),
    )
