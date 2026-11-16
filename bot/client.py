from __future__ import annotations

import asyncio
import logging
import math
from collections import Counter
from datetime import timedelta
from typing import AbstractSet, Dict, List, NamedTuple, Optional
from urllib.parse import urldefrag, urlsplit

import discord
from discord import app_commands
from discord.ext import commands

from .config import BotConfig
from .formatter import (
    DESCRIPTION_LIMIT,
    MESSAGE_CONTENT_LIMIT,
    add_field,
    create_error_embed,
    create_job_embed,
    fit_embed,
    trim,
)
from .models import JobPosting
from .history import PostHistory, normalize_url
from .limits import (
    MAX_URLS_PER_REQUEST,
    RateLimitDecision,
    RequestRateLimiter,
    is_trusted,
    max_urls_for,
)
from .openai_client import OpenAIJobParser, OpenAIRequestError, OpenAITruncatedError
from .retry import PendingRequest, RetryManager
from .scraping import fetch_page_text
from .utils import extract_image_urls, extract_urls, sanitize_team

logger = logging.getLogger(__name__)

RETRY_QUEUED_NOTE = (
    "OpenAI was unavailable for part of this request: it stays queued and will be retried "
    "automatically the next time the bot restarts."
)


class JobCollection(NamedTuple):
    jobs: List[Dict[str, object]]
    issues: List[str]
    # A transient OpenAI failure happened: the request should stay queued for a retry.
    retryable_failure: bool = False


def _https_url(candidate: object) -> Optional[str]:
    """Return ``candidate`` stripped if it is an absolute https URL without credentials."""
    if not isinstance(candidate, str):
        return None
    value = candidate.strip()
    if not value or "\\" in value or any(char.isspace() for char in value):
        return None
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError on an invalid port
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    return value


def resolve_page_job_url(
    candidate: object,
    source_url: str,
    page_links: AbstractSet[str] = frozenset(),
) -> str:
    """Keep the model's link only if it is https and either links from the page itself
    (e.g. an external job board) or sits on the page's host (or a subdomain of it)."""
    url = _https_url(candidate)
    if not url:
        return source_url
    if urldefrag(url).url in page_links:
        return url
    source_host = (urlsplit(source_url).hostname or "").lower()
    base_host = source_host[4:] if source_host.startswith("www.") else source_host
    if base_host:
        host = (urlsplit(url).hostname or "").lower()
        if host in (source_host, base_host) or host.endswith("." + base_host):
            return url
    return source_url


def resolve_image_job_url(candidate: object, source_ref: str) -> str:
    """Keep the model's link only if it is an absolute https URL."""
    return _https_url(candidate) or source_ref


def _reset_links_shared_by_page_jobs(items: List[Dict[str, object]], source_url: str) -> None:
    """Point jobs back to the page when several of them got the same link.

    The model is asked for the link of each specific posting: one link given to several
    different jobs (a social profile, a "similar jobs" offer, a common apply page) is not
    that, so the page listing them is used instead.
    """
    source_key = normalize_url(source_url)
    counts = Counter(normalize_url(str(item.get("job_url") or "")) for item in items)
    for item in items:
        key = normalize_url(str(item.get("job_url") or ""))
        if key and key != source_key and counts[key] > 1:
            logger.warning(
                "🔗 '%s' shared link %s with other jobs from %s; using the page instead",
                item.get("job_title"),
                item.get("job_url"),
                source_url,
            )
            item["job_url"] = source_url


def _mark_shared_job_urls(items: List[Dict[str, object]]) -> None:
    """Flag jobs from one source that share a link (the page, the poster, a common apply page).

    Their duplicate key becomes link + company + title, so the second and third offers are
    not mistaken for duplicates; every other job is keyed by its link alone. The flag is
    always overwritten so the model cannot set it.
    """
    counts = Counter(normalize_url(str(item.get("job_url") or "")) for item in items)
    for item in items:
        item["shared_link"] = counts[normalize_url(str(item.get("job_url") or ""))] > 1


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
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            # Model output ends up in messages: never let it ping anyone.
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.parser = parser
        self.retry_manager = retry_manager
        self.post_history = post_history
        self.rate_limiter = RequestRateLimiter(
            max_per_hour=config.max_requests_per_hour,
            max_per_day=config.max_requests_per_day,
        )
        self.team_channels: Dict[str, int] = {}
        self._register_app_commands()
        self._ready_logged = False

    def _register_app_commands(self) -> None:
        # Discord only honours guild_only/default_permissions on the top-level group.
        # /jobbot is open to every member; post and preview are rate-limited instead.
        jobbot_group = app_commands.Group(
            name="jobbot",
            description="Manage and debug la-commu-discord-bot",
            guild_only=True,
        )

        @jobbot_group.command(name="status", description="Show bot configuration for this server")
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
                description=trim("\n".join(mapping_lines), DESCRIPTION_LIMIT) or "No team channels configured.",
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

            trusted = self._is_trusted(interaction)
            if not await self._start_limited_request(interaction, trusted=trusted, command="post"):
                return
            max_urls = max_urls_for(trusted)

            await self.retry_manager.start_request(
                request_id=interaction.id,
                guild_id=guild.id,
                user_id=interaction.user.id,
                reference=reference,
                max_urls=max_urls,
            )
            request_summary = reference.strip().splitlines()[0][:200]
            logger.info(
                "🎯 jobbot post by %s (interaction %s): %s",
                interaction.user,
                interaction.id,
                request_summary,
            )
            try:
                collected = await self._collect_jobs(
                    reference=reference,
                    max_urls=max_urls,
                )
                jobs_data, issues = collected.jobs, collected.issues
                if collected.retryable_failure:
                    issues.append(RETRY_QUEUED_NOTE)

                if not jobs_data:
                    if issues:
                        logger.warning("📝 Job parsing produced no results: %s", " | ".join(issues))
                    embed = create_error_embed(
                        title="Post failed",
                        description="No job listings were detected.",
                        details="\n".join(issues) if issues else "Inspect the provided reference and try again.",
                    )
                    await self._safe_followup(interaction, embed=embed, ephemeral=True)
                    await self._finish_request(interaction.id, retryable_failure=collected.retryable_failure)
                    return

                posted_jobs, dispatch_issues = await self._post_jobs(jobs_data, guild)
                issues.extend(dispatch_issues)
                logger.info(
                    "📚 jobbot post result interaction %s: posted=%d issues=%d",
                    interaction.id,
                    len(posted_jobs),
                    len(issues),
                )

                embed = self._build_post_summary_embed(posted_jobs, issues)
                await self._safe_followup(interaction, embed=embed, ephemeral=True)
                await self._finish_request(interaction.id, retryable_failure=collected.retryable_failure)
            except Exception as exc:  # noqa: BLE001
                await self.retry_manager.fail_request(interaction.id, repr(exc))
                raise

        @jobbot_group.command(name="preview", description="Preview channel routing for a job reference")
        @app_commands.describe(
            reference="Paste the job URL or text containing job links (use 'image: https://...' for posters)",
        )
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

            trusted = self._is_trusted(interaction)
            if not await self._start_limited_request(interaction, trusted=trusted, command="preview"):
                return

            request_summary = reference.strip().splitlines()[0][:200]
            logger.info(
                "🎯 jobbot preview by %s (interaction %s): %s",
                interaction.user,
                interaction.id,
                request_summary,
            )
            collected = await self._collect_jobs(
                reference=reference,
                max_urls=max_urls_for(trusted),
            )
            jobs_data, issues = collected.jobs, collected.issues

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

            embed = discord.Embed(
                title="🧪 Job preview",
                description=trim("\n".join(lines), DESCRIPTION_LIMIT),
                color=discord.Color.blurple(),
            )
            add_field(embed, name="Jobs Found", value=str(len(jobs_data)), inline=True)
            if issues:
                add_field(embed, name="Notes", value="\n".join(issues), inline=False)
            fit_embed(embed)
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
            job = JobPosting.from_dict(data)
            origin = job.job_url or job.source_url or ""
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
            return
        except discord.NotFound:
            logger.warning("⚠️ Interaction expired before followup could be sent")
            return
        except discord.HTTPException as exc:
            logger.warning("⚠️ Discord rejected the followup embed: %s", exc)
            status = exc.status

        fallback = trim(
            f"{embed.title or 'Job Caster'}: the full response could not be displayed "
            f"(Discord error {status}). Check the bot logs for details.",
            MESSAGE_CONTENT_LIMIT,
        )
        try:
            await interaction.followup.send(content=fallback, ephemeral=ephemeral)
        except discord.HTTPException as fallback_exc:
            logger.error("🚫 Fallback followup failed too: %s", fallback_exc)

    def _is_trusted(self, interaction: discord.Interaction) -> bool:
        return is_trusted(
            interaction.user.id,
            interaction.permissions,
            self.config.trusted_user_ids,
        )

    async def _start_limited_request(
        self,
        interaction: discord.Interaction,
        *,
        trusted: bool,
        command: str,
    ) -> bool:
        """Apply the rate limit, then defer. Returns False when the command must stop.

        The slot taken from the limiter is given back if the interaction expired before defer.
        """
        decision = None
        if not trusted:
            decision = self.rate_limiter.acquire(interaction.user.id)
            if not decision.allowed:
                await self._send_rate_limit_notice(interaction, decision)
                return False
        try:
            await interaction.response.defer(thinking=True, ephemeral=True)
        except discord.NotFound:
            logger.warning("⚠️ Interaction expired before defer in jobbot_%s", command)
            if decision is not None:
                self.rate_limiter.release(interaction.user.id, decision)
            return False
        return True

    async def _send_rate_limit_notice(
        self,
        interaction: discord.Interaction,
        decision: RateLimitDecision,
    ) -> None:
        retry_at = discord.utils.utcnow() + timedelta(seconds=math.ceil(decision.retry_after))
        logger.info(
            "⏳ Rate limited %s (%s/%s), retry in %.0fs",
            interaction.user,
            decision.limit,
            decision.window,
            decision.retry_after,
        )
        message = (
            f"⏳ You've reached the limit of {decision.limit} /jobbot requests per {decision.window}. "
            f"You can try again {discord.utils.format_dt(retry_at, 'R')} "
            f"({discord.utils.format_dt(retry_at, 't')})."
        )
        try:
            await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException as exc:
            logger.warning("⚠️ Could not send rate-limit notice: %s", exc)

    async def _finish_request(self, request_id: int, *, retryable_failure: bool) -> None:
        """A transient OpenAI failure keeps the request pending so it is retried on the next start."""
        if retryable_failure:
            await self.retry_manager.fail_request(request_id, "OpenAI request failed")
        else:
            await self.retry_manager.complete_request(request_id)

    def _build_post_summary_embed(
        self,
        posted_jobs: List[tuple[JobPosting, discord.TextChannel]],
        issues: List[str],
    ) -> discord.Embed:
        if posted_jobs:
            description = "\n".join(
                f"• {job.job_title} @ {job.company_name} → {channel.mention}"
                for job, channel in posted_jobs
            )
        else:
            description = "No jobs were posted because of routing errors."

        embed = discord.Embed(
            title="📬 Job posting summary",
            description=trim(description, DESCRIPTION_LIMIT),
            color=discord.Color.green() if posted_jobs else discord.Color.orange(),
        )
        add_field(embed, name="Jobs Posted", value=str(len(posted_jobs)), inline=True)
        if issues:
            add_field(embed, name="Notes", value="\n".join(issues), inline=False)
        return fit_embed(embed)

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
                    await self.retry_manager.fail_request(
                        entry.request_id,
                        "Retry produced no jobs or OpenAI failed",
                    )
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
        collected = await self._collect_jobs(
            reference=entry.reference,
            max_urls=entry.max_urls or MAX_URLS_PER_REQUEST,
        )
        jobs_data, issues = collected.jobs, collected.issues
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
        # Stay pending after a transient OpenAI failure; history skips jobs already posted.
        return not collected.retryable_failure

    async def _parse_page_jobs(self, url: str) -> tuple[List[Dict[str, object]], Optional[str]]:
        """Raises OpenAIRequestError when the OpenAI call itself fails."""
        logger.info("🌐 Parsing job page: %s", url)
        page = await fetch_page_text(
            url,
            timeout=self.config.request_timeout,
            max_bytes=self.config.max_scrape_bytes,
        )
        if not page:
            return [], f"Couldn't fetch content from {url}"

        # The retry-on-restart path re-fetches the page, so it gets fresh links too.
        jobs_data = await self.parser.parse_from_text(
            content=page.text,
            url=url,
            links=page.links,
            link_labels=page.link_labels,
        )
        if not jobs_data:
            return [], f"Couldn't parse job details from {url}"

        page_links = frozenset(page.links)
        for item in jobs_data:
            item["job_url"] = resolve_page_job_url(item.get("job_url"), url, page_links)
            item["source_url"] = url
        _reset_links_shared_by_page_jobs(jobs_data, url)
        _mark_shared_job_urls(jobs_data)
        logger.info("📦 Parsed %d job(s) from %s", len(jobs_data), url)
        return jobs_data, None

    async def _parse_image_jobs(
        self,
        url: str,
    ) -> tuple[List[Dict[str, object]], Optional[str]]:
        """Raises OpenAIRequestError when the OpenAI call itself fails."""
        logger.info("🖼️ Parsing job image: %s", url)
        jobs_data = await self.parser.parse_from_image(
            image_url=url,
            url=url,
        )
        if not jobs_data:
            return [], f"Couldn't parse job details from the image {url}"

        for item in jobs_data:
            item["job_url"] = resolve_image_job_url(item.get("job_url"), url)
            item["source_url"] = url
        _mark_shared_job_urls(jobs_data)
        logger.info("📦 Parsed %d job(s) from image %s", len(jobs_data), url)
        return jobs_data, None

    async def _collect_jobs(
        self,
        *,
        reference: str,
        max_urls: int = MAX_URLS_PER_REQUEST,
    ) -> JobCollection:
        jobs: List[Dict[str, object]] = []
        issues: List[str] = []
        retryable_failure = False

        text = reference.strip()
        image_urls = extract_image_urls(text)
        all_urls = list(dict.fromkeys([*extract_urls(text), *image_urls]))
        if len(all_urls) > max_urls:
            ignored = len(all_urls) - max_urls
            all_urls = all_urls[:max_urls]
            issues.append(
                f"Only the first {max_urls} URLs were processed; {ignored} extra URL(s) were ignored."
            )
        image_urls = [url for url in image_urls if url in all_urls]
        page_urls = [url for url in all_urls if url not in image_urls]

        sources = [(url, self._parse_page_jobs) for url in page_urls]
        sources += [(url, self._parse_image_jobs) for url in image_urls]
        for url, parse in sources:
            try:
                parsed, error = await parse(url)
            except OpenAITruncatedError as exc:
                issues.append(
                    f"OpenAI response was truncated ({exc.error_type}) for {url}; nothing was posted from it."
                )
                continue
            except OpenAIRequestError as exc:
                retryable_failure = retryable_failure or exc.retryable
                issues.append(f"OpenAI request failed ({exc.error_type}) for {url}")
                continue
            if error:
                issues.append(error)
            else:
                jobs.extend(parsed)

        if not page_urls and not image_urls:
            issues.append("No URLs detected in reference text.")

        return JobCollection(jobs, issues, retryable_failure)
