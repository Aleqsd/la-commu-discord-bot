from bot.models import JobPosting


def test_job_posting_from_dict_defaults():
    data = {
        "job_title": "Senior Programmer",
        "company_name": "Space Cats",
        "job_url": "https://jobs.example.com/123",
        "team": "Dev",
        "skills": ["C++", "UE5"],
        "known_titles": "Space Quest, Astro Cats",
    }

    posting = JobPosting.from_dict(data)

    assert posting.job_title == "Senior Programmer"
    assert posting.company_name == "Space Cats"
    assert posting.job_url == "https://jobs.example.com/123"
    assert posting.team == "dev"
    assert posting.skills == ["C++", "UE5"]
    assert posting.known_titles == ["Space Quest", "Astro Cats"]


def test_job_posting_from_dict_missing_fields():
    posting = JobPosting.from_dict({})
    assert posting.job_title == "Unknown Role"
    assert posting.company_name == "Unknown Studio"
    assert posting.team == "dev"
    assert posting.job_url == ""


def test_job_posting_from_dict_coerces_unexpected_types():
    posting = JobPosting.from_dict(
        {
            "job_title": "  Technical Artist  ",
            "company_name": 42,
            "team": ["Art", "dev"],
            "location": ["Paris", " Remote "],
            "compensation": {"min": 40000, "max": 50000},
            "remote_friendly": "yes",
            "skills": ["Houdini", 3, {"nested": True}, ["x"], "  "],
            "known_titles": 7,
            "description_summary": True,
            "job_url": None,
            "source_url": " https://studio.example.com/careers ",
        }
    )

    assert posting.job_title == "Technical Artist"
    assert posting.company_name == "42"
    assert posting.team == "art"
    assert posting.location == "Paris, Remote"
    assert posting.compensation is None
    assert posting.remote_friendly is True
    assert posting.skills == ["Houdini", "3"]
    assert posting.known_titles == ["7"]
    assert posting.description_summary is None
    assert posting.job_url == "https://studio.example.com/careers"
    assert posting.source_url == "https://studio.example.com/careers"


def test_job_posting_from_dict_numeric_team_and_bool_strings():
    posting = JobPosting.from_dict({"team": 3, "remote_friendly": "No", "job_title": ""})
    assert posting.team == "3"
    assert posting.remote_friendly is False
    assert posting.job_title == "Unknown Role"
