import discord

from bot.formatter import add_field, create_error_embed, create_job_embed, fit_embed, trim
from bot.models import JobPosting


def _sample_job() -> JobPosting:
    return JobPosting(
        job_title="Lead Artist",
        company_name="Pixel Forge",
        job_url="https://jobs.example.com/artist",
        team="art",
        location="Remote",
        work_model="Remote",
        seniority="Lead",
        contract_type="Full-time",
        description_summary="Craft stylised worlds for our next title.",
        skills=["Photoshop", "Blender"],
        known_titles=["Skybound Saga"],
    )


def test_create_job_embed_basic_fields():
    embed = create_job_embed(_sample_job())

    assert isinstance(embed, discord.Embed)
    assert embed.title.startswith("🎨 Lead Artist")
    assert embed.url == "https://jobs.example.com/artist"
    assert embed.author.name == "Pixel Forge"
    # Ensure footer mentions team
    assert "Team: art" in embed.footer.text


def test_create_error_embed_details():
    embed = create_error_embed(
        title="Fetch failed",
        description="Could not read job page.",
        details="URL: https://jobs.example.com",
    )
    assert embed.title.startswith("⚠️")
    assert embed.description == "Could not read job page."
    assert embed.fields[0].value == "URL: https://jobs.example.com"
    assert embed.color == discord.Color.red()


def test_trim_marks_cuts():
    assert trim("abcdef", 10) == "abcdef"
    assert trim("abcdef", 4) == "abc…"
    assert trim(None, 5) == ""
    assert len(trim("x" * 5000, 1024)) == 1024


def test_job_embed_respects_discord_limits():
    job = JobPosting(
        job_title="T" * 400,
        company_name="C" * 400,
        job_url="https://jobs.example.com/artist",
        team="art",
        location="L" * 2000,
        seniority="   ",
        contract_type="Full-time",
        compensation="$" * 2000,
        description_summary="D" * 5000,
        skills=["S" * 600, "K" * 600],
        known_titles=["G" * 2000],
    )

    embed = create_job_embed(job)

    assert len(embed.title) <= 256
    assert len(embed.author.name) <= 256
    assert len(embed.description) <= 4096
    assert all(0 < len(field.value) <= 1024 for field in embed.fields)
    assert all(0 < len(field.name) <= 256 for field in embed.fields)
    assert "📈 Seniority" not in [field.name for field in embed.fields]  # blank value skipped
    assert len(embed) <= 6000


def test_fit_embed_drops_trailing_fields_when_needed():
    embed = discord.Embed(title="t", description="d" * 10)
    for index in range(8):
        add_field(embed, name=f"f{index}", value="v" * 1024, inline=False)

    fit_embed(embed)

    assert len(embed) <= 6000
    assert embed.fields[0].name == "f0"


def test_error_embed_details_are_trimmed():
    embed = create_error_embed(title="Post failed", description="x" * 5000, details="line\n" * 1000)
    assert len(embed.description) <= 4096
    assert len(embed.fields[0].value) <= 1024
    assert len(embed) <= 6000
