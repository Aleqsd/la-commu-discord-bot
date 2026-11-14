from __future__ import annotations

from typing import Any, Optional

import discord

from .models import JobPosting

ERROR_COLOR = discord.Color.red()

# Discord limits (characters).
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
AUTHOR_NAME_LIMIT = 256
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024
FOOTER_TEXT_LIMIT = 2048
EMBED_TOTAL_LIMIT = 6000
MESSAGE_CONTENT_LIMIT = 2000
ELLIPSIS = "…"

TEAM_EMOJIS = {
    "art": "🎨",
    "game_design": "🧠",
    "dev": "💻",
    "others": "🧩",
}

CATEGORY_LABELS = {
    "work_model": "🏡 Work Model",
    "location": "📍 Location",
    "seniority": "📈 Seniority",
    "contract_type": "📜 Contract",
    "compensation": "💰 Compensation",
    "skills": "🛠️ Skills",
    "known_titles": "🎮 Known Titles",
}


def trim(value: Optional[Any], limit: int) -> str:
    """Return ``value`` as text of at most ``limit`` characters, marking cuts with an ellipsis."""
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    if limit <= len(ELLIPSIS):
        return text[:limit]
    return text[: limit - len(ELLIPSIS)] + ELLIPSIS


def add_field(embed: discord.Embed, *, name: str, value: Optional[Any], inline: bool) -> None:
    """Add a field within Discord limits; fields whose name or value would be empty are skipped."""
    name_text = trim(name, FIELD_NAME_LIMIT).strip()
    value_text = trim(value, FIELD_VALUE_LIMIT).strip()
    if not name_text or not value_text:
        return
    embed.add_field(name=name_text, value=value_text, inline=inline)


def fit_embed(embed: discord.Embed) -> discord.Embed:
    """Keep the embed under the 6000-character total: shorten the description, then drop trailing fields."""
    overflow = len(embed) - EMBED_TOTAL_LIMIT
    if overflow > 0 and embed.description:
        keep = max(len(embed.description) - overflow, 0)
        embed.description = trim(embed.description, keep) or None
        overflow = len(embed) - EMBED_TOTAL_LIMIT
    while overflow > 0 and embed.fields:
        embed.remove_field(len(embed.fields) - 1)
        overflow = len(embed) - EMBED_TOTAL_LIMIT
    return embed


def _format_list(items):
    if not items:
        return None
    return ", ".join(items)


def create_job_embed(job: JobPosting) -> discord.Embed:
    emoji = TEAM_EMOJIS.get(job.team, "🎯")
    title = f"{emoji} {job.job_title}" if emoji else job.job_title
    embed = discord.Embed(
        title=trim(title, TITLE_LIMIT),
        url=job.job_url or None,
        color=discord.Color.blurple(),
    )
    embed.description = trim(job.description_summary or "", DESCRIPTION_LIMIT)
    embed.set_author(name=trim(job.company_name, AUTHOR_NAME_LIMIT))

    if job.work_model or job.remote_friendly is not None:
        work_model = job.work_model or ("Remote" if job.remote_friendly else "Onsite")
        add_field(embed, name=CATEGORY_LABELS["work_model"], value=work_model, inline=True)
    add_field(embed, name=CATEGORY_LABELS["location"], value=job.location, inline=True)
    add_field(embed, name=CATEGORY_LABELS["seniority"], value=job.seniority, inline=True)
    add_field(embed, name=CATEGORY_LABELS["contract_type"], value=job.contract_type, inline=True)
    add_field(embed, name=CATEGORY_LABELS["compensation"], value=job.compensation, inline=True)
    add_field(embed, name=CATEGORY_LABELS["skills"], value=_format_list(job.skills), inline=False)
    add_field(embed, name=CATEGORY_LABELS["known_titles"], value=_format_list(job.known_titles), inline=False)

    embed.set_footer(text=trim(f"Team: {job.team}", FOOTER_TEXT_LIMIT))
    return fit_embed(embed)


def create_error_embed(title: str, description: str, *, details: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=trim(f"⚠️ {title}", TITLE_LIMIT),
        description=trim(description, DESCRIPTION_LIMIT),
        color=ERROR_COLOR,
    )
    if details:
        add_field(embed, name="Details", value=details, inline=False)
    return fit_embed(embed)
