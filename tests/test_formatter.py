import discord

from bot.formatter import create_error_embed, create_job_embed
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
