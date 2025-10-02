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
