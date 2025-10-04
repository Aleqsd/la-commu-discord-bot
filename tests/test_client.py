from pathlib import Path

import pytest

from bot.client import LaCommuDiscordBot
from bot.config import BotConfig, ChannelConfig, OpenAIConfig
from bot.history import PostHistory
from bot.retry import RetryManager


class DummyParser:
    async def parse_from_text(self, *, content: str, url: str):  # pragma: no cover - not used here
        return []

    async def parse_from_image(self, *, image_url: str, url: str):  # pragma: no cover - not used here
        return []


class FakeGuild:
    def __init__(self, channels):
        self.id = 123
        self.text_channels = channels
        self.name = "Test Guild"

    def get_channel(self, channel_id):
        for channel in self.text_channels:
            if channel.id == channel_id:
                return channel
        return None

    async def fetch_channel(self, channel_id):
        return self.get_channel(channel_id)


class FakeChannel:
    def __init__(self, name, channel_id):
        self.name = name
        self.id = channel_id
        self.sent_embeds = []

    async def send(self, *, embed, reference=None, mention_author=None):
        self.sent_embeds.append(embed)


@pytest.mark.asyncio
async def test_dispatch_jobs_posts_to_mapped_channel(tmp_path: Path):
    config = BotConfig(
        discord_token="dummy",
        openai=OpenAIConfig(api_key="dummy"),
        channels=ChannelConfig(
            team_channels={
                "art": 1,
            },
        ),
    )
    parser = DummyParser()
    manager = RetryManager(tmp_path / "pending.json")
    post_history = PostHistory(tmp_path / "posted.log")
    await post_history.load()
    bot = LaCommuDiscordBot(config, parser, manager, post_history)

    art_channel = FakeChannel("art", 1)
    guild = FakeGuild([art_channel])

    await bot._cache_team_channels(guild)

    jobs_data = [
        {
            "job_title": "Environment Artist",
            "company_name": "Voxel Labs",
            "job_url": "https://jobs.example.com/env",
            "team": "art",
        }
    ]

    posted, issues = await bot._post_jobs(jobs_data, guild)

    assert len(posted) == 1
    assert not issues
    assert len(art_channel.sent_embeds) == 1
    job, channel = posted[0]
    assert job.job_title == "Environment Artist"
    assert channel is art_channel
    embed = art_channel.sent_embeds[0]
    assert "Environment Artist" in embed.title


@pytest.mark.asyncio
async def test_dispatch_jobs_missing_channel_reports_error(tmp_path: Path):
    config = BotConfig(
        discord_token="dummy",
        openai=OpenAIConfig(api_key="dummy"),
        channels=ChannelConfig(
            team_channels={
                "art": 99,
            },
        ),
    )
    parser = DummyParser()
    manager = RetryManager(tmp_path / "pending.json")
    post_history = PostHistory(tmp_path / "posted.log")
    await post_history.load()
    bot = LaCommuDiscordBot(config, parser, manager, post_history)

    guild = FakeGuild([])

    await bot._cache_team_channels(guild)

    jobs_data = [
        {
            "job_title": "Environment Artist",
            "company_name": "Voxel Labs",
            "job_url": "https://jobs.example.com/env",
            "team": "art",
        }
    ]

    posted, issues = await bot._post_jobs(jobs_data, guild)

    assert not posted
    assert len(issues) == 1
    

@pytest.mark.asyncio
async def test_dispatch_jobs_skips_duplicates(tmp_path: Path):
    config = BotConfig(
        discord_token="dummy",
        openai=OpenAIConfig(api_key="dummy"),
        channels=ChannelConfig(
            team_channels={
                "art": 1,
            },
        ),
    )
    parser = DummyParser()
    manager = RetryManager(tmp_path / "pending.json")
    post_history = PostHistory(tmp_path / "posted.log")
    await post_history.load()
    bot = LaCommuDiscordBot(config, parser, manager, post_history)

    art_channel = FakeChannel("art", 1)
    guild = FakeGuild([art_channel])
    await bot._cache_team_channels(guild)

    job_payload = {
        "job_title": "Environment Artist",
        "company_name": "Voxel Labs",
        "job_url": "https://jobs.example.com/env",
        "team": "art",
    }

    posted, issues = await bot._post_jobs([job_payload], guild)
    assert len(posted) == 1
    assert not issues

    second_post, second_issues = await bot._post_jobs([job_payload], guild)
    assert not second_post
    assert any("Skipped duplicate" in item for item in second_issues)
    assert len(art_channel.sent_embeds) == 1
