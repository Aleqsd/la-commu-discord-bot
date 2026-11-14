from pathlib import Path

import pytest

from bot.history import PostHistory, build_job_key, build_legacy_key, normalize_url
from bot.models import JobPosting


def _job(
    title="Environment Artist",
    company="Voxel Labs",
    job_url="",
    source_url=None,
    team="art",
    shared_link=False,
):
    return JobPosting(
        job_title=title,
        company_name=company,
        job_url=job_url,
        team=team,
        source_url=source_url,
        shared_link=shared_link,
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "HTTP://WWW.Example.COM/Jobs/42/?utm_source=x&b=2&a=1&trackingId=abc&refId=r&fbclid=f&gclid=g#apply",
            "https://example.com/Jobs/42?a=1&b=2",
        ),
        ("https://example.com/jobs/42/", "https://example.com/jobs/42"),
        ("http://example.com/jobs/42", "https://example.com/jobs/42"),
        ("https://www.example.com/jobs/42#top", "https://example.com/jobs/42"),
        ("https://example.com/jobs?UTM_Campaign=x", "https://example.com/jobs"),
        ("https://example.com:8443/jobs", "https://example.com:8443/jobs"),
        ("https://example.com:443/jobs", "https://example.com/jobs"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_linkedin_style_variants_share_a_key():
    a = _job(job_url="https://www.linkedin.com/jobs/view/123/?trackingId=abc%3D%3D&refId=xyz")
    b = _job(job_url="http://linkedin.com/jobs/view/123")
    assert build_job_key(a) == build_job_key(b) == "https://linkedin.com/jobs/view/123"


def test_key_is_the_job_url_alone():
    job = _job(job_url="https://studio.com/jobs/7", source_url="https://studio.com/careers")
    assert build_job_key(job) == "https://studio.com/jobs/7"


def test_single_job_page_is_keyed_by_url_alone():
    # The only job on a page falls back to the page URL: still keyed by that URL alone.
    job = _job(job_url="https://studio.com/careers/lead", source_url="https://studio.com/careers/lead")
    assert build_job_key(job) == "https://studio.com/careers/lead"


def test_shared_link_jobs_add_company_and_title():
    job = _job(
        title="  Senior   3D Artist ",
        company="Voxel LABS",
        job_url="https://www.studio.com/careers/?utm_source=discord",
        source_url="https://studio.com/careers",
        shared_link=True,
    )
    assert build_job_key(job) == "https://studio.com/careers|voxel labs|senior 3d artist"


def test_legacy_key_only_for_url_keyed_jobs():
    assert build_legacy_key(_job(job_url="https://Jobs.Example.com/Env/#apply")) == "https://jobs.example.com/env"
    assert build_legacy_key(_job(job_url="https://studio.com/careers", shared_link=True)) == ""
    assert build_legacy_key(_job(job_url="")) == ""


@pytest.mark.asyncio
async def test_three_jobs_from_one_image_are_not_duplicates(tmp_path: Path):
    history = PostHistory(tmp_path / "posted.log")
    image = "https://cdn.example.com/poster.png"
    jobs = [
        _job(title=title, company="Pixel Forge", job_url=image, source_url=image, shared_link=True)
        for title in ("Concept Artist", "Gameplay Programmer", "Level Designer")
    ]

    for job in jobs:
        assert not await history.is_posted(job)
        await history.mark_posted(job)

    for job in jobs:
        assert await history.is_posted(job)

    lines = (tmp_path / "posted.log").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "https://cdn.example.com/poster.png|pixel forge|concept artist",
        "https://cdn.example.com/poster.png|pixel forge|gameplay programmer",
        "https://cdn.example.com/poster.png|pixel forge|level designer",
    ]


@pytest.mark.asyncio
async def test_legacy_history_lines_still_block_duplicates(tmp_path: Path):
    storage = tmp_path / "posted.log"
    # Line written by the previous algorithm (lowercased URL, no fragment, no trailing slash).
    storage.write_text("https://jobs.example.com/env\n", encoding="utf-8")
    history = PostHistory(storage)

    assert await history.is_posted(_job(job_url="https://JOBS.example.com/env/#apply"))
    assert not await history.is_posted(_job(job_url="https://jobs.example.com/other"))


@pytest.mark.asyncio
async def test_legacy_line_does_not_block_shared_link_jobs(tmp_path: Path):
    storage = tmp_path / "posted.log"
    # The old bot recorded the careers page itself when it posted its first job.
    storage.write_text("https://studio.example.com/careers\n", encoding="utf-8")
    history = PostHistory(storage)
    careers = "https://studio.example.com/careers"

    for title in ("Animator", "Rigger"):
        assert not await history.is_posted(
            _job(title=title, job_url=careers, source_url=careers, shared_link=True)
        )
    assert await history.is_posted(_job(job_url=careers, source_url=careers))


@pytest.mark.asyncio
async def test_reposting_a_single_job_url_is_caught_despite_new_wording(tmp_path: Path):
    history = PostHistory(tmp_path / "posted.log")
    url = "https://studio.example.com/jobs/lead-artist"
    await history.mark_posted(_job(title="Lead Artist", company="Studio", job_url=url, source_url=url))

    reworded = _job(title="Lead 2D Artist (M/F)", company="Studio Inc.", job_url=url + "/", source_url=url)
    assert await history.is_posted(reworded)


@pytest.mark.asyncio
async def test_only_the_new_key_is_written(tmp_path: Path):
    storage = tmp_path / "posted.log"
    history = PostHistory(storage)
    job = _job(job_url="https://Jobs.example.com/Env/?utm_medium=social")

    await history.mark_posted(job)
    await history.mark_posted(job)

    assert storage.read_text(encoding="utf-8").splitlines() == ["https://jobs.example.com/Env"]


@pytest.mark.asyncio
async def test_tracking_variants_are_detected_as_duplicates(tmp_path: Path):
    history = PostHistory(tmp_path / "posted.log")
    await history.mark_posted(_job(job_url="https://www.studio.com/jobs/12?gclid=abc"))

    assert await history.is_posted(_job(job_url="http://studio.com/jobs/12/#details"))
