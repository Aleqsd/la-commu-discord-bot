from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from .config import BotConfig
from .formatter import create_job_embed, create_error_embed
from .models import JobPosting
from .history import PostHistory
from .openai_client import OpenAIJobParser
from .retry import PendingRequest, RetryManager
from .scraping import fetch_page_text
from .utils import extract_image_urls, extract_urls, sanitize_team

logger = logging.getLogger(__name__)


class LaCommuDiscordBot(commands.Bot):
    def __init__(
        self,
        config: BotConfig,
        parser: OpenAIJobParser,
        retry_manager: RetryManager,
        post_history: PostHistory,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.parser = parser
        self.retry_manager = retry_manager
        self.post_history = post_history
        self.team_channels: Dict[str, int] = {}
        self._register_app_commands()
        self._ready_logged = False

    def _register_app_commands(self) -> None:
        jobbot_group = app_commands.Group(
            name="jobbot",
            description="Manage and debug la-commu-discord-bot",
        )

        @jobbot_group.command(name="status", description="Show bot configuration for this server")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        async def jobbot_status(interaction: discord.Interaction) -> None:
            guild = interaction.guild
            if not guild:
                await interaction.response.send_message(
                    "Run this command inside a Discord server.",
                    ephemeral=True,
                )
                return

            mapping_lines = []
            for team, channel_id in self.config.channels.team_channels.items():
                channel = guild.get_channel(channel_id)
                status_icon = "✅" if channel else "⚠️"
                descriptor = channel.mention if channel else f"id:{channel_id}"
                mapping_lines.append(f"{status_icon} `{team}` → {descriptor}")

            embed = discord.Embed(
                title="📊 la-commu-discord-bot status",
                description="\n".join(mapping_lines) or "No team channels configured.",
                color=discord.Color.blurple(),
            )
            cached_count = sum(
                1 for key in self.team_channels if key.startswith(f"{guild.id}:")
            )
            embed.set_footer(text=f"Cached routes: {cached_count}")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @jobbot_group.command(name="post", description="Parse and distribute job offers")
        @app_commands.describe(
            reference="Paste the job URL or text containing job links (use 'image: https://...' for posters)",
        )
        @app_commands.guild_only()
        async def jobbot_post(
            interaction: discord.Interaction,
            reference: str,
        ) -> None:
            guild = interaction.guild
            if not guild:
                await interaction.response.send_message(
                    "Run this command inside a Discord server.",
                    ephemeral=True,
                )
                return

            try:
                await interaction.response.defer(thinking=True, ephemeral=True)
            except discord.NotFound:
                logger.warning("⚠️ Interaction expired before defer in jobbot_post")
                return
            await self.retry_manager.start_request(
                request_id=interaction.id,
                guild_id=guild.id,
                user_id=interaction.user.id,
                reference=reference,
            )
            request_summary = reference.strip().splitlines()[0][:200]
            logger.info(
                "🎯 jobbot post by %s (interaction %s): %s",
                interaction.user,
                interaction.id,
                request_summary,
            )
            try:
                jobs_data, issues = await self._collect_jobs(
                    reference=reference,
                )

                if not jobs_data:
                    if issues:
                        logger.warning("📝 Job parsing produced no results: %s", " | ".join(issues))
                    embed = create_error_embed(
                        title="Post failed",
                        description="No job listings were detected.",
                        details="\n".join(issues) if issues else "Inspect the provided reference and try again.",
                    )
                    await self._safe_followup(interaction, embed=embed, ephemeral=True)
                    await self.retry_manager.complete_request(interaction.id)
                    return

                posted_jobs, dispatch_issues = await self._post_jobs(jobs_data, guild)
                issues.extend(dispatch_issues)
                logger.info(
                    "📚 jobbot post result interaction %s: posted=%d issues=%d",
                    interaction.id,
                    len(posted_jobs),
                    len(issues),
                )

                if posted_jobs:
                    lines = [
                        f"• {job.job_title} @ {job.company_name} → {channel.mention}"
                        for job, channel in posted_jobs
                    ]
                    description = "\n".join(lines)
                else:
                    description = "No jobs were posted because of routing errors."

                embed = discord.Embed(
                    title="📬 Job posting summary",
                    description=description,
                    color=discord.Color.green() if posted_jobs else discord.Color.orange(),
                )
                embed.add_field(name="Jobs Posted", value=str(len(posted_jobs)), inline=True)
                if issues:
                    embed.add_field(
                        name="Notes",
                        value="\n".join(issues)[:1000],
                        inline=False,
                    )

                await self._safe_followup(interaction, embed=embed, ephemeral=True)
                await self.retry_manager.complete_request(interaction.id)
            except Exception as exc:  # noqa: BLE001
                await self.retry_manager.fail_request(interaction.id, repr(exc))
                raise

        @jobbot_group.command(name="preview", description="Preview channel routing for a job reference")
        @app_commands.describe(
            reference="Paste the job URL or text containing job links (use 'image: https://...' for posters)",
        )
        @app_commands.guild_only()
        async def jobbot_preview(
            interaction: discord.Interaction,
            reference: str,
        ) -> None:
            guild = interaction.guild
            if not guild:
                await interaction.response.send_message(
                    "Run this command inside a Discord server.",
                    ephemeral=True,
                )
                return

            try:
                await interaction.response.defer(thinking=True, ephemeral=True)
            except discord.NotFound:
                logger.warning("⚠️ Interaction expired before defer in jobbot_preview")
                return
            request_summary = reference.strip().splitlines()[0][:200]
            logger.info(
                "🎯 jobbot preview by %s (interaction %s): %s",
                interaction.user,
                interaction.id,
                request_summary,
            )
            jobs_data, issues = await self._collect_jobs(
                reference=reference,
            )

            if not jobs_data:
                if issues:
                    logger.warning("📝 Job preview produced no results: %s", " | ".join(issues))
                embed = create_error_embed(
                    title="Preview failed",
                    description="No job listings were detected.",
                    details="\n".join(issues) if issues else "Inspect the provided reference and try again.",
                )
                await self._safe_followup(interaction, embed=embed, ephemeral=True)
                return

            lines = []
            for data in jobs_data:
                job = JobPosting.from_dict(data)
                channel_id = self.config.channels.team_channels.get(job.team)
                channel = guild.get_channel(channel_id) if channel_id else None
                target = channel.mention if channel else (
                    f"id:{channel_id}" if channel_id else f"{job.team} (unmapped)"
                )
                origin = job.job_url or job.company_name
                lines.append(
                    f"• {job.job_title} @ {job.company_name} → {target}\n  Source: {origin}"
                )

            description = "\n".join(lines)
            if len(description) > 3800:
                description = description[:3797] + "…"

            embed = discord.Embed(
                title="🧪 Job preview",
                description=description,
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Jobs Found", value=str(len(jobs_data)), inline=True)
            if issues:
                embed.add_field(name="Notes", value="\n".join(issues)[:1000], inline=False)
            logger.info(
                "📚 jobbot preview result interaction %s: jobs=%d issues=%d",
                interaction.id,
                len(jobs_data),
                len(issues),
            )
            await self._safe_followup(interaction, embed=embed, ephemeral=True)

        self.tree.add_command(jobbot_group)

    async def setup_hook(self) -> None:
        await self._cache_team_channels()
        await self.tree.sync()
        asyncio.create_task(self._resume_pending_requests())

    async def on_ready(self) -> None:
        if not self._ready_logged:
            logger.info("🚀 Logged in as %s (ID: %s)", self.user, self.user and self.user.id)
            self._ready_logged = True
        else:
            logger.debug("🔁 Session resumed as %s (ID: %s)", self.user, self.user and self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        await self.process_commands(message)

    async def _post_jobs(
        self,
        jobs_data: List[Dict[str, object]],
        guild: discord.Guild,
    ) -> tuple[List[tuple[JobPosting, discord.TextChannel]], List[str]]:
        posted_jobs: List[tuple[JobPosting, discord.TextChannel]] = []
        issues: List[str] = []

        for data in jobs_data:
            origin = data.get("job_url") or data.get("source_url") or ""
            data.setdefault("job_url", origin)
            data.setdefault("source_url", origin)
            job = JobPosting.from_dict(data)
            team_key = sanitize_team(job.team)

            if await self.post_history.is_posted(job):
                issues.append(
                    f"Skipped duplicate `{job.job_title}` ({origin or 'unknown origin'})."
                )
                logger.info(
                    "🔁 Duplicate job ignored: '%s' origin=%s guild=%s",
                    job.job_title,
                    origin or "unknown",
                    guild.id,
                )
                continue

            channel = await self._resolve_team_channel(guild, team_key)
            if not channel:
                issues.append(
                    f"No destination channel mapped for team `{team_key}` (origin: {origin or 'unknown'})."
                )
                logger.warning(
                    "🏷️ Missing channel for team %s in guild %s",
                    team_key,
                    guild.id,
                )
                continue

            embed = create_job_embed(job)
            try:
                await channel.send(embed=embed)
            except discord.HTTPException as exc:
                issues.append(
                    f"Failed to post `{job.job_title}` to #{channel.name}: {exc}"
                )
                logger.error(
                    "🚫 Failed to post '%s' to #%s in guild %s: %s",
                    job.job_title,
                    channel.name,
                    guild.id,
                    exc,
                )
                continue

            posted_jobs.append((job, channel))
            logger.info(
                "📤 Posted '%s' to #%s in guild %s",
                job.job_title,
                channel.name,
                guild.id,
            )
            await self.post_history.mark_posted(job)

        return posted_jobs, issues

    async def _cache_team_channels(self, guild: Optional[discord.Guild] = None) -> None:
        if guild:
            logger.info("🗂️ Refreshing team channels for guild %s", guild.id)
            keys_to_remove = [key for key in self.team_channels if key.startswith(f"{guild.id}:")]
            for key in keys_to_remove:
                self.team_channels.pop(key, None)
            guilds = [guild]
        else:
            logger.info("🗂️ Caching team channels for %d guild(s)", len(self.guilds))
            self.team_channels.clear()
            guilds = list(self.guilds)

        for active_guild in guilds:
            for team, channel_id in self.config.channels.team_channels.items():
                if channel_id <= 0:
                    continue
                channel = active_guild.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await active_guild.fetch_channel(channel_id)
                    except discord.NotFound:
                        logger.warning(
                            "⚠️ Channel id %s for team %s not found in guild %s",
                            channel_id,
                            team,
                            active_guild.id,
                        )
                        continue
                    except discord.Forbidden:
                        logger.warning(
                            "⚠️ Missing permissions to fetch channel %s for team %s in guild %s",
                            channel_id,
                            team,
                            active_guild.id,
                        )
                        continue
                    except discord.InvalidData:
                        logger.warning(
                            "⚠️ Channel id %s configured for team %s belongs to a different guild",
                            channel_id,
                            team,
                        )
                        continue
                    except discord.HTTPException as exc:
                        logger.warning(
                            "⚠️ Failed HTTP fetch for channel %s team %s in guild %s: %s",
                            channel_id,
                            team,
                            active_guild.id,
                            exc,
                        )
                        continue
                if channel is None:
                    logger.warning(
                        "⚠️ Channel id %s for team %s still unresolved in guild %s",
                        channel_id,
                        team,
                        active_guild.id,
                    )
                    continue

                self.team_channels[f"{active_guild.id}:{team}"] = channel.id
                logger.info(
                    "📌 Mapped %s to #%s in guild %s",
                    team,
                    getattr(channel, "name", channel.id),
                    active_guild.id,
                )

    async def _resolve_team_channel(self, guild: discord.Guild, team: str) -> Optional[discord.TextChannel]:
        key = f"{guild.id}:{team}"
        channel_id = self.team_channels.get(key)
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                return channel  # type: ignore[return-value]

        configured_id = self.config.channels.team_channels.get(team)
        if not configured_id:
            return None

        channel = guild.get_channel(configured_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(configured_id)
            except discord.NotFound:
                logger.warning(
                    "⚠️ Channel id %s for team %s not found in guild %s",
                    configured_id,
                    team,
                    guild.id,
                )
                return None
            except discord.Forbidden:
                logger.warning(
                    "⚠️ Missing permissions to fetch channel %s for team %s in guild %s",
                    configured_id,
                    team,
                    guild.id,
                )
                return None
            except discord.InvalidData:
                logger.warning(
                    "⚠️ Channel id %s configured for team %s belongs to a different guild",
                    configured_id,
                    team,
                )
                return None
            except discord.HTTPException as exc:
                logger.warning(
                    "⚠️ Failed HTTP fetch for channel %s team %s in guild %s: %s",
                    configured_id,
                    team,
                    guild.id,
                    exc,
                )
                return None
        if channel is None:
            logger.warning(
                "⚠️ Channel id %s for team %s could not be resolved in guild %s",
                configured_id,
                team,
                guild.id,
            )
            return None

        self.team_channels[key] = channel.id
        logger.info(
            "📌 Cached channel #%s for team %s in guild %s",
            getattr(channel, "name", channel.id),
            team,
            guild.id,
        )
        return channel  # type: ignore[return-value]

    async def _safe_followup(
        self,
        interaction: discord.Interaction,
        *,
        embed: discord.Embed,
        ephemeral: bool,
    ) -> None:
        try:
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        except discord.NotFound:
            logger.warning("⚠️ Interaction expired before followup could be sent")

    async def _resume_pending_requests(self) -> None:
        await self.wait_until_ready()
        pending = await self.retry_manager.list_pending_requests()
        if not pending:
            return
        logger.info("🔁 Resuming %d pending job request(s)", len(pending))
        for entry in pending:
            if entry.attempts >= self.retry_manager.max_attempts:
                logger.warning(
                    "⚠️ Skipping request %s after %s attempts (last error: %s)",
                    entry.request_id,
                    entry.attempts,
                    entry.last_error,
                )
                continue
            try:
                success = await self._retry_request(entry)
            except Exception as exc:  # noqa: BLE001
                await self.retry_manager.fail_request(entry.request_id, repr(exc))
                logger.exception("🚫 Retry failed for request %s", entry.request_id)
            else:
                if success:
                    await self.retry_manager.complete_request(entry.request_id)
                else:
                    await self.retry_manager.fail_request(entry.request_id, "Retry produced no jobs")
            await asyncio.sleep(1)

    async def _retry_request(self, entry: PendingRequest) -> bool:
        guild = self.get_guild(entry.guild_id)
        if guild is None:
            try:
                guild = await self.fetch_guild(entry.guild_id)
            except discord.DiscordException as exc:
                logger.warning(
                    "⚠️ Retry request %s: cannot access guild %s (%s)",
                    entry.request_id,
                    entry.guild_id,
                    exc,
                )
                return False
        logger.info(
            "🔁 Retrying request %s for guild %s (attempt %s/%s)",
            entry.request_id,
            entry.guild_id,
            entry.attempts + 1,
            self.retry_manager.max_attempts,
        )
        jobs_data, issues = await self._collect_jobs(reference=entry.reference)
        if not jobs_data:
            if issues:
                logger.warning(
                    "📝 Retry request %s produced no jobs: %s",
                    entry.request_id,
                    " | ".join(issues),
                )
            return False
        posted_jobs, dispatch_issues = await self._post_jobs(jobs_data, guild)
        issues.extend(dispatch_issues)
        logger.info(
            "📚 Retry request %s result: posted=%d issues=%d",
            entry.request_id,
            len(posted_jobs),
            len(issues),
        )
        return True

    async def _parse_page_jobs(self, url: str) -> tuple[List[Dict[str, object]], Optional[str]]:
        logger.info("🌐 Parsing job page: %s", url)
        content = await fetch_page_text(
            url,
            timeout=self.config.request_timeout,
            max_bytes=self.config.max_scrape_bytes,
        )
        if not content:
            return [], f"Couldn't fetch content from {url}"

        jobs_data = await self.parser.parse_from_text(content=content, url=url)
        if not jobs_data:
            return [], f"Couldn't parse job details from {url}"

        for item in jobs_data:
            item.setdefault("job_url", url)
            item.setdefault("source_url", url)
        logger.info("📦 Parsed %d job(s) from %s", len(jobs_data), url)
        return jobs_data, None

    async def _parse_image_jobs(
        self,
        url: str,
    ) -> tuple[List[Dict[str, object]], Optional[str]]:
        logger.info("🖼️ Parsing job image: %s", url)
        jobs_data = await self.parser.parse_from_image(
            image_url=url,
            url=url,
        )
        if not jobs_data:
            return [], f"Couldn't parse job details from the image {url}"

        for item in jobs_data:
            item.setdefault("job_url", url)
            item.setdefault("source_url", url)
        logger.info("📦 Parsed %d job(s) from image %s", len(jobs_data), url)
        return jobs_data, None

    async def _collect_jobs(
        self,
        *,
        reference: str,
    ) -> tuple[List[Dict[str, object]], List[str]]:
        jobs: List[Dict[str, object]] = []
        issues: List[str] = []

        text = reference.strip()
        image_urls = extract_image_urls(text)
        all_urls = extract_urls(text)
        page_urls = [url for url in all_urls if url not in image_urls]

        for url in page_urls:
            parsed, error = await self._parse_page_jobs(url)
            if error:
                issues.append(error)
            else:
                jobs.extend(parsed)

        for image_url in image_urls:
            parsed, error = await self._parse_image_jobs(image_url)
            if error:
                issues.append(error)
            else:
                jobs.extend(parsed)

        if not page_urls and not image_urls:
            issues.append("No URLs detected in reference text.")

        return jobs, issues
