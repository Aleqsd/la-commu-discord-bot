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


class _FakeResponses:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class _Response:
            output_text = '[{"job_title": "Producer"}]'

        return _Response()


def _parser_with_fake_client(temperature):
    from types import SimpleNamespace

    from bot.config import OpenAIConfig
    from bot.openai_client import OpenAIJobParser

    parser = OpenAIJobParser(OpenAIConfig(api_key="test-key", temperature=temperature))
    responses = _FakeResponses()
    parser._client = SimpleNamespace(responses=responses)
    return parser, responses


def test_request_omits_temperature_by_default():
    import asyncio

    parser, responses = _parser_with_fake_client(temperature=None)

    jobs = asyncio.run(parser.parse_from_text(content="Producer wanted", url="https://example.com"))

    assert jobs == [{"job_title": "Producer"}]
    assert responses.calls[0]["model"] == "gpt-6.1-terra"
    assert "temperature" not in responses.calls[0]


def test_request_sends_temperature_when_configured():
    import asyncio

    parser, responses = _parser_with_fake_client(temperature=0.1)

    asyncio.run(parser.parse_from_text(content="Producer wanted", url="https://example.com"))

    assert responses.calls[0]["temperature"] == 0.1


def test_config_leaves_temperature_unset_without_env(monkeypatch):
    from bot.config import load_config

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord")
    monkeypatch.setenv("OPENAI_API_KEY", "openai")
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    config = load_config()

    assert config.openai.temperature is None
    assert config.openai.model == "gpt-6.1-terra"
