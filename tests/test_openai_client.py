import json

import pytest

from bot.openai_client import _extract_jobs


def test_extract_jobs_from_array():
    payload = json.dumps([
        {"job_title": "Producer", "team": "others"},
        {"job_title": "Gameplay Programmer", "team": "dev"},
    ])
    result = _extract_jobs(payload)
    assert len(result) == 2
    assert result[0]["job_title"] == "Producer"
    assert result[1]["team"] == "dev"


def test_extract_jobs_from_single_object():
    payload = json.dumps({"job_title": "Designer", "team": "game_design"})
    result = _extract_jobs(payload)
    assert len(result) == 1
    assert result[0]["team"] == "game_design"


def test_extract_jobs_handles_malformed_json():
    result = _extract_jobs("{not valid json}")
    assert result == []


def test_extract_jobs_missing_objects_logs_warning(caplog):
    payload = json.dumps(["not", "objects"])
    with caplog.at_level("WARNING"):
        result = _extract_jobs(payload)
    assert result == []
    assert "JSON array contained no dict objects" in caplog.text
