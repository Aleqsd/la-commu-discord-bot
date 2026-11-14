from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from bot import client as client_module
from bot.client import LaCommuDiscordBot, resolve_image_job_url, resolve_page_job_url
from bot.config import BotConfig, ChannelConfig, OpenAIConfig
from bot.history import PostHistory
from bot.limits import RequestRateLimiter
from bot.openai_client import OpenAIRequestError
from bot.retry import RetryManager


class DummyParser:
    async def parse_from_text(self, *, content: str, url: str):  # pragma: no cover - not used here
        return []

    async def parse_from_image(self, *, image_url: str, url: str):  # pragma: no cover - not used here
        return []


class RecordingParser:
    def __init__(self, *, text_jobs=None, image_jobs=None, error=None):
        self.text_jobs = text_jobs or []
        self.image_jobs = image_jobs or []
        self.error = error
        self.text_calls = []
        self.image_calls = []

    async def parse_from_text(self, *, content: str, url: str):
        self.text_calls.append(url)
        if self.error:
            raise self.error
        return [dict(job) for job in self.text_jobs]

    async def parse_from_image(self, *, image_url: str, url: str):
        self.image_calls.append(image_url)
        if self.error:
            raise self.error
        return [dict(job) for job in self.image_jobs]


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
        self.mention = f"<#{channel_id}>"
        self.sent_embeds = []

    async def send(self, *, embed, reference=None, mention_author=None):
        self.sent_embeds.append(embed)


class FakeResponse:
    def __init__(self):
        self.deferred = False
        self.messages = []

    async def defer(self, **kwargs):
        self.deferred = True

    async def send_message(self, content=None, **kwargs):
        self.messages.append((content, kwargs))


class FakeFollowup:
    def __init__(self, errors=()):
        self.errors = list(errors)
        self.sent = []

    async def send(self, content=None, *, embed=None, ephemeral=False):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append({"content": content, "embed": embed, "ephemeral": ephemeral})


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id

    def __str__(self):
        return f"user-{self.id}"


class FakeInteraction:
    _next_id = 1000

    def __init__(self, guild, *, user_id=42, permissions=None, followup_errors=()):
        FakeInteraction._next_id += 1
        self.id = FakeInteraction._next_id
        self.guild = guild
        self.user = FakeUser(user_id)
        self.permissions = permissions or discord.Permissions(send_messages=True)
        self.response = FakeResponse()
        self.followup = FakeFollowup(followup_errors)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def http_error(status=400, cls=discord.HTTPException):
    return cls(SimpleNamespace(status=status, reason="Bad Request"), "Invalid Form Body")


async def make_bot(tmp_path: Path, parser=None, **config_kwargs):
    config = BotConfig(
        discord_token="dummy",
        openai=OpenAIConfig(api_key="dummy"),
        channels=ChannelConfig(team_channels={"art": 1, "dev": 2}),
        **config_kwargs,
    )
    post_history = PostHistory(tmp_path / "posted.log")
    await post_history.load()
    bot = LaCommuDiscordBot(config, parser or DummyParser(), RetryManager(tmp_path / "pending.json"), post_history)
    guild = FakeGuild([FakeChannel("art", 1), FakeChannel("dev", 2)])
    await bot._cache_team_channels(guild)
    return bot, guild


def command(bot, name):
    return bot.tree.get_command("jobbot").get_command(name)


@pytest.fixture
def fake_fetch(monkeypatch):
    fetched = []

    async def fetch(url, *, timeout, max_bytes):
        fetched.append(url)
        return "page text"

    monkeypatch.setattr(client_module, "fetch_page_text", fetch)
    return fetched


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


# --- Permissions and limits -------------------------------------------------------------


@pytest.mark.asyncio
async def test_jobbot_group_is_guild_only_and_open_to_everyone(tmp_path: Path):
    bot, _ = await make_bot(tmp_path)
    group = bot.tree.get_command("jobbot")

    assert group.guild_only is True
    assert group.default_permissions is None
    payload = group.to_dict(bot.tree)
    assert payload["dm_permission"] is False
    assert payload["default_member_permissions"] is None
    for sub in group.commands:
        assert sub.default_permissions is None
        assert sub.guild_only is False


@pytest.mark.asyncio
async def test_bot_never_pings(tmp_path: Path):
    bot, _ = await make_bot(tmp_path)
    assert bot.allowed_mentions.to_dict() == {"parse": []}


@pytest.mark.asyncio
async def test_regular_member_is_rate_limited_on_post_and_preview(tmp_path: Path):
    parser = RecordingParser(image_jobs=[{"job_title": "Animator", "team": "art"}])
    bot, guild = await make_bot(tmp_path, parser)
    clock = FakeClock()
    bot.rate_limiter = RequestRateLimiter(max_per_hour=2, max_per_day=10, clock=clock)
    reference = "image: https://cdn.example.com/poster.png"

    await command(bot, "post").callback(FakeInteraction(guild), reference)
    await command(bot, "preview").callback(FakeInteraction(guild), reference)
    blocked = FakeInteraction(guild)
    await command(bot, "post").callback(blocked, reference)

    assert len(parser.image_calls) == 2
    assert not blocked.response.deferred
    content, kwargs = blocked.response.messages[0]
    assert kwargs == {"ephemeral": True}
    assert "2 /jobbot requests per hour" in content
    assert "<t:" in content
    assert await bot.retry_manager.list_pending_requests() == []

    clock.now += 3600
    await command(bot, "preview").callback(FakeInteraction(guild), reference)
    assert len(parser.image_calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permissions, user_id, trusted_ids",
    [
        (discord.Permissions(manage_guild=True), 42, frozenset()),
        (discord.Permissions(administrator=True), 42, frozenset()),
        (discord.Permissions(send_messages=True), 42, frozenset({42})),
    ],
)
async def test_trusted_members_bypass_rate_limit(tmp_path: Path, permissions, user_id, trusted_ids):
    parser = RecordingParser(image_jobs=[{"job_title": "Animator", "team": "art"}])
    bot, guild = await make_bot(tmp_path, parser, trusted_user_ids=trusted_ids)
    bot.rate_limiter = RequestRateLimiter(max_per_hour=1, max_per_day=1, clock=FakeClock())

    for _ in range(3):
        interaction = FakeInteraction(guild, user_id=user_id, permissions=permissions)
        await command(bot, "preview").callback(interaction, "image: https://cdn.example.com/poster.png")
        assert interaction.response.messages == []

    assert len(parser.image_calls) == 3


@pytest.mark.asyncio
async def test_status_is_not_rate_limited(tmp_path: Path):
    bot, guild = await make_bot(tmp_path)
    bot.rate_limiter = RequestRateLimiter(max_per_hour=1, max_per_day=1, clock=FakeClock())

    for _ in range(3):
        interaction = FakeInteraction(guild)
        await command(bot, "status").callback(interaction)
        _, kwargs = interaction.response.messages[0]
        assert "embed" in kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permissions, expected",
    [(discord.Permissions(send_messages=True), 20), (discord.Permissions(manage_guild=True), 50)],
)
async def test_url_cap_per_request(tmp_path: Path, fake_fetch, permissions, expected):
    parser = RecordingParser()
    bot, guild = await make_bot(tmp_path, parser)
    urls = [f"https://jobs.example.com/{index}" for index in range(60)]
    interaction = FakeInteraction(guild, permissions=permissions)

    await command(bot, "preview").callback(interaction, " ".join(urls))

    assert fake_fetch == urls[:expected]
    details = interaction.followup.sent[0]["embed"].fields[0].value
    assert f"Only the first {expected} URLs were processed; {60 - expected} extra URL(s) were ignored." in details


@pytest.mark.asyncio
async def test_retry_reuses_the_stored_url_cap(tmp_path: Path, fake_fetch):
    parser = RecordingParser(text_jobs=[{"job_title": "Animator", "team": "art"}])
    bot, guild = await make_bot(tmp_path, parser)
    bot.get_guild = lambda guild_id: guild
    urls = " ".join(f"https://jobs.example.com/{index}" for index in range(30))
    await bot.retry_manager.start_request(request_id=1, guild_id=guild.id, user_id=5, reference=urls, max_urls=25)

    entry = (await bot.retry_manager.list_pending_requests())[0]
    assert await bot._retry_request(entry)
    assert len(fake_fetch) == 25


# --- Duplicates and embed links -------------------------------------------------------


@pytest.mark.asyncio
async def test_three_jobs_from_one_image_are_all_posted_once(tmp_path: Path):
    parser = RecordingParser(
        image_jobs=[
            {"job_title": "Concept Artist", "company_name": "Pixel Forge", "team": "art"},
            {"job_title": "Gameplay Programmer", "company_name": "Pixel Forge", "team": "dev"},
            {"job_title": "Character Artist", "company_name": "Pixel Forge", "team": "art"},
        ]
    )
    bot, guild = await make_bot(tmp_path, parser)
    reference = "image: https://cdn.example.com/poster.png"

    collected = await bot._collect_jobs(reference=reference)
    posted, issues = await bot._post_jobs(collected.jobs, guild)
    assert [job.job_title for job, _ in posted] == ["Concept Artist", "Gameplay Programmer", "Character Artist"]
    assert not issues

    collected = await bot._collect_jobs(reference=reference)
    posted, issues = await bot._post_jobs(collected.jobs, guild)
    assert posted == []
    assert len([issue for issue in issues if "Skipped duplicate" in issue]) == 3


@pytest.mark.asyncio
async def test_jobs_sharing_one_apply_link_are_not_duplicates(tmp_path: Path, fake_fetch):
    shared = "https://studio.example.com/apply"
    parser = RecordingParser(
        text_jobs=[
            {"job_title": "Animator", "company_name": "Studio", "job_url": shared, "team": "art"},
            {"job_title": "Rigger", "company_name": "Studio", "job_url": shared, "team": "art"},
        ]
    )
    bot, guild = await make_bot(tmp_path, parser)

    collected = await bot._collect_jobs(reference="https://studio.example.com/careers")
    posted, _ = await bot._post_jobs(collected.jobs, guild)

    assert [job.job_title for job, _ in posted] == ["Animator", "Rigger"]
    assert all(job.job_url == shared for job, _ in posted)


@pytest.mark.parametrize(
    "candidate, expected",
    [
        ("https://studio.example.com/jobs/1", "https://studio.example.com/jobs/1"),
        ("https://careers.studio.example.com/jobs/1", "https://careers.studio.example.com/jobs/1"),
        ("https://evil.example.net/login", "https://www.studio.example.com/careers"),
        ("https://studio.example.com.evil.net/x", "https://www.studio.example.com/careers"),
        ("https://studio.example.com@evil.net/x", "https://www.studio.example.com/careers"),
        ("http://studio.example.com/jobs/1", "https://www.studio.example.com/careers"),
        ("studio.example.com/jobs/1", "https://www.studio.example.com/careers"),
        ("/jobs/1", "https://www.studio.example.com/careers"),
        ("javascript:alert(1)", "https://www.studio.example.com/careers"),
        (["https://studio.example.com/jobs/1"], "https://www.studio.example.com/careers"),
        (None, "https://www.studio.example.com/careers"),
    ],
)
def test_page_job_url_must_be_https_on_the_same_host(candidate, expected):
    assert resolve_page_job_url(candidate, "https://www.studio.example.com/careers") == expected


@pytest.mark.parametrize(
    "candidate, expected",
    [
        ("https://jobs.anywhere.example/apply", "https://jobs.anywhere.example/apply"),
        ("http://jobs.anywhere.example/apply", "https://cdn.example.com/poster.png"),
        ("jobs.anywhere.example/apply", "https://cdn.example.com/poster.png"),
        ("mailto:jobs@studio.example.com", "https://cdn.example.com/poster.png"),
        ("", "https://cdn.example.com/poster.png"),
    ],
)
def test_image_job_url_must_be_absolute_https(candidate, expected):
    assert resolve_image_job_url(candidate, "https://cdn.example.com/poster.png") == expected


@pytest.mark.asyncio
async def test_page_jobs_get_validated_links(tmp_path: Path, fake_fetch):
    parser = RecordingParser(
        text_jobs=[
            {"job_title": "A", "job_url": "https://evil.example.net/phish"},
            {"job_title": "B", "job_url": "https://jobs.studio.example.com/b"},
        ]
    )
    bot, _ = await make_bot(tmp_path, parser)

    collected = await bot._collect_jobs(reference="see https://studio.example.com/careers")

    assert [job["job_url"] for job in collected.jobs] == [
        "https://studio.example.com/careers",
        "https://jobs.studio.example.com/b",
    ]
    assert all(job["source_url"] == "https://studio.example.com/careers" for job in collected.jobs)


# --- Follow-ups and OpenAI failures ---------------------------------------------------


@pytest.mark.asyncio
async def test_safe_followup_falls_back_to_short_message(tmp_path: Path):
    bot, guild = await make_bot(tmp_path)
    interaction = FakeInteraction(guild, followup_errors=[http_error(400)])
    embed = discord.Embed(title="📬 Job posting summary", description="x")

    await bot._safe_followup(interaction, embed=embed, ephemeral=True)

    assert len(interaction.followup.sent) == 1
    fallback = interaction.followup.sent[0]
    assert fallback["embed"] is None
    assert fallback["ephemeral"] is True
    assert "could not be displayed" in fallback["content"]
    assert len(fallback["content"]) <= 2000


@pytest.mark.asyncio
async def test_safe_followup_gives_up_quietly_when_everything_fails(tmp_path: Path):
    bot, guild = await make_bot(tmp_path)
    interaction = FakeInteraction(guild, followup_errors=[http_error(400), http_error(500)])

    await bot._safe_followup(interaction, embed=discord.Embed(title="t"), ephemeral=True)

    assert interaction.followup.sent == []


@pytest.mark.asyncio
async def test_safe_followup_does_not_retry_expired_interactions(tmp_path: Path):
    bot, guild = await make_bot(tmp_path)
    interaction = FakeInteraction(guild, followup_errors=[http_error(404, discord.NotFound)])

    await bot._safe_followup(interaction, embed=discord.Embed(title="t"), ephemeral=True)

    assert interaction.followup.sent == []
    assert interaction.followup.errors == []


@pytest.mark.asyncio
async def test_summary_embed_stays_within_limits(tmp_path: Path):
    bot, guild = await make_bot(tmp_path)
    channel = guild.text_channels[0]
    posted = [
        (client_module.JobPosting.from_dict({"job_title": "T" * 200, "company_name": "C" * 200}), channel)
        for _ in range(40)
    ]
    issues = [f"Skipped duplicate `{'x' * 100}`" for _ in range(50)]

    embed = bot._build_post_summary_embed(posted, issues)

    assert len(embed.description) <= 4096
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert len(embed) <= 6000


@pytest.mark.asyncio
async def test_openai_failure_is_reported_and_request_stays_pending(tmp_path: Path):
    parser = RecordingParser(error=OpenAIRequestError("APITimeoutError"))
    bot, guild = await make_bot(tmp_path, parser)
    interaction = FakeInteraction(guild)

    await command(bot, "post").callback(interaction, "image: https://cdn.example.com/poster.png")

    details = interaction.followup.sent[0]["embed"].fields[0].value
    assert "OpenAI request failed (APITimeoutError)" in details
    assert "Couldn't parse job details" not in details
    pending = await bot.retry_manager.list_pending_requests()
    assert [entry.request_id for entry in pending] == [interaction.id]
    assert pending[0].attempts == 1
    assert pending[0].max_urls == 20


@pytest.mark.asyncio
async def test_successful_post_completes_the_request(tmp_path: Path):
    parser = RecordingParser(image_jobs=[{"job_title": "Animator", "team": "art"}])
    bot, guild = await make_bot(tmp_path, parser)
    interaction = FakeInteraction(guild)

    await command(bot, "post").callback(interaction, "image: https://cdn.example.com/poster.png")

    assert await bot.retry_manager.list_pending_requests() == []
    assert interaction.followup.sent[0]["embed"].title == "📬 Job posting summary"


@pytest.mark.asyncio
async def test_retry_with_openai_failure_is_not_marked_done(tmp_path: Path):
    parser = RecordingParser(error=OpenAIRequestError("RateLimitError"))
    bot, guild = await make_bot(tmp_path, parser)
    bot.get_guild = lambda guild_id: guild
    await bot.retry_manager.start_request(
        request_id=7,
        guild_id=guild.id,
        user_id=5,
        reference="image: https://cdn.example.com/poster.png",
    )

    entry = (await bot.retry_manager.list_pending_requests())[0]
    assert await bot._retry_request(entry) is False
