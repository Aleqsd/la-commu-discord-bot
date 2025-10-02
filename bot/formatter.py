from __future__ import annotations

import discord

from .models import JobPosting

ERROR_COLOR = discord.Color.red()

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


def _format_list(items):
    if not items:
        return None
    return ", ".join(items)


def create_job_embed(job: JobPosting) -> discord.Embed:
    emoji = TEAM_EMOJIS.get(job.team, "🎯")
    title = f"{emoji} {job.job_title}" if emoji else job.job_title
    embed = discord.Embed(title=title, url=job.job_url or None, color=discord.Color.blurple())
    embed.description = job.description_summary or ""
    embed.set_author(name=job.company_name)

    if job.work_model or job.remote_friendly is not None:
        work_model = job.work_model or ("Remote" if job.remote_friendly else "Onsite")
        embed.add_field(name=CATEGORY_LABELS["work_model"], value=work_model, inline=True)
    if job.location:
        embed.add_field(name=CATEGORY_LABELS["location"], value=job.location, inline=True)
    if job.seniority:
        embed.add_field(name=CATEGORY_LABELS["seniority"], value=job.seniority, inline=True)
    if job.contract_type:
        embed.add_field(name=CATEGORY_LABELS["contract_type"], value=job.contract_type, inline=True)
    if job.compensation:
        embed.add_field(name=CATEGORY_LABELS["compensation"], value=job.compensation, inline=True)

    skills_text = _format_list(job.skills)
    if skills_text:
        embed.add_field(name=CATEGORY_LABELS["skills"], value=skills_text, inline=False)
    titles_text = _format_list(job.known_titles)
    if titles_text:
        embed.add_field(name=CATEGORY_LABELS["known_titles"], value=titles_text, inline=False)

    embed.set_footer(text=f"Team: {job.team}")
    return embed


def create_error_embed(title: str, description: str, *, details: str | None = None) -> discord.Embed:
    embed = discord.Embed(title=f"⚠️ {title}", description=description, color=ERROR_COLOR)
    if details:
        embed.add_field(name="Details", value=details, inline=False)
    return embed
