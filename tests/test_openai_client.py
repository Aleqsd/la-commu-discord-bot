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


def test_extract_jobs_from_jobs_object():
    payload = json.dumps({"jobs": [{"job_title": "Producer"}, {"job_title": "Animator"}]})
    assert [job["job_title"] for job in _extract_jobs(payload)] == ["Producer", "Animator"]


def test_extract_jobs_single_object_containing_an_array():
    payload = json.dumps({"job_title": "Tech Artist", "skills": ["Houdini", "Python"]})
    result = _extract_jobs(payload)
    assert result == [{"job_title": "Tech Artist", "skills": ["Houdini", "Python"]}]


def test_extract_jobs_with_text_around_the_json():
    text = (
        "Here is what I found [see note 1]:\n"
        '{"jobs": [{"job_title": "Level Designer", "skills": ["Unreal"]}]}\n'
        "Let me know if you need more."
    )
    assert _extract_jobs(text) == [{"job_title": "Level Designer", "skills": ["Unreal"]}]


def test_extract_jobs_in_code_fence():
    text = '```json\n{"jobs": [{"job_title": "QA Lead"}]}\n```'
    assert _extract_jobs(text) == [{"job_title": "QA Lead"}]


def test_extract_jobs_empty_jobs_list():
    assert _extract_jobs('{"jobs": []}') == []


class _FakeResponses:
    def __init__(self, response=None, error=None) -> None:
        self.calls = []
        self._response = response
        self._error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response

        class _Response:
            output_text = '{"jobs": [{"job_title": "Producer"}]}'

        return _Response()


def _parser_with_fake_client(temperature=None, response=None, error=None):
    from types import SimpleNamespace

    from bot.config import OpenAIConfig
    from bot.openai_client import OpenAIJobParser

    parser = OpenAIJobParser(OpenAIConfig(api_key="test-key", temperature=temperature))
    responses = _FakeResponses(response=response, error=error)
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


def test_request_asks_for_a_json_object():
    import asyncio

    parser, responses = _parser_with_fake_client()

    asyncio.run(parser.parse_from_image(image_url="https://cdn.example.com/a.png", url="https://cdn.example.com/a.png"))
    asyncio.run(parser.parse_from_text(content="Producer wanted", url="https://example.com"))

    for call in responses.calls:
        assert call["text"] == {"format": {"type": "json_object"}}
        prompt_text = json.dumps(call["input"])
        assert "JSON" in prompt_text
        assert '{\\"jobs\\": [...]}' in prompt_text


def test_prompt_keeps_up_to_30000_characters():
    import asyncio

    from bot.openai_client import MAX_PROMPT_CHARS

    parser, responses = _parser_with_fake_client()
    content = "a" * 29_000 + "TAIL" + "b" * 5_000

    asyncio.run(parser.parse_from_text(content=content, url="https://example.com"))

    assert MAX_PROMPT_CHARS == 30_000
    assert "TAIL" in responses.calls[0]["input"][1]["content"]


def test_client_uses_configured_timeout_and_single_retry():
    from bot.config import OpenAIConfig
    from bot.openai_client import OpenAIJobParser

    parser = OpenAIJobParser(OpenAIConfig(api_key="test-key"), timeout=12.5)

    assert parser._client.timeout == 12.5
    assert parser._client.max_retries == 1


def test_request_errors_are_raised_with_their_type(caplog):
    import asyncio

    import httpx
    import openai

    from bot.openai_client import OpenAIRequestError

    error = openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    parser, _ = _parser_with_fake_client(error=error)

    with pytest.raises(OpenAIRequestError) as excinfo:
        asyncio.run(parser.parse_from_text(content="Producer wanted", url="https://example.com"))

    assert excinfo.value.error_type == "APITimeoutError"
    assert excinfo.value.retryable is True


def test_incomplete_response_is_discarded(caplog):
    import asyncio
    from types import SimpleNamespace

    from bot.openai_client import OpenAITruncatedError

    # Cut off after the first job: none of it may be used.
    response = SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        output_text='{"jobs": [{"job_title": "Producer"}, {"job_title": "Anim',
    )
    parser, _ = _parser_with_fake_client(response=response)

    with caplog.at_level("WARNING"), pytest.raises(OpenAITruncatedError) as excinfo:
        asyncio.run(parser.parse_from_text(content="Producer wanted", url="https://example.com"))

    assert excinfo.value.error_type == "max_output_tokens"
    assert excinfo.value.retryable is False
    assert "max_output_tokens" in caplog.text


def test_request_allows_16000_output_tokens():
    import asyncio

    parser, responses = _parser_with_fake_client()

    asyncio.run(parser.parse_from_text(content="Producer wanted", url="https://example.com"))

    assert responses.calls[0]["max_output_tokens"] == 16000


def _api_error(cls, status):
    import httpx

    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return cls("boom", response=httpx.Response(status, request=request), body=None)


def _error_cases():
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return [
        (openai.APIConnectionError(request=request), True),
        (openai.APITimeoutError(request=request), True),
        (_api_error(openai.RateLimitError, 429), True),
        (_api_error(openai.InternalServerError, 500), True),
        (_api_error(openai.APIStatusError, 503), True),
        (_api_error(openai.BadRequestError, 400), False),
        (_api_error(openai.AuthenticationError, 401), False),
        (_api_error(openai.NotFoundError, 404), False),
        (ValueError("unexpected"), False),
    ]


@pytest.mark.parametrize("error, retryable", _error_cases())
def test_only_transient_errors_are_retryable(error, retryable):
    import asyncio

    from bot.openai_client import OpenAIRequestError

    parser, _ = _parser_with_fake_client(error=error)

    with pytest.raises(OpenAIRequestError) as excinfo:
        asyncio.run(parser.parse_from_image(image_url="https://cdn.example.com/a.png", url="https://cdn.example.com/a.png"))

    assert excinfo.value.error_type == type(error).__name__
    assert excinfo.value.retryable is retryable


def test_extract_jobs_ignores_inner_objects_of_cut_off_json():
    assert _extract_jobs('{"jobs": [{"job_title": "A"}, {"job_title": "B"}, {"job_tit') == []
    assert _extract_jobs('Here you go: [{"job_title": "A"}, {"job_title": "B"') == []
    assert _extract_jobs('{"jobs": [{"job_title": "A", "skills": ["x"]}') == []


def test_extract_jobs_brackets_inside_strings_do_not_confuse_the_scan():
    text = 'Result [1]: {"jobs": [{"job_title": "Artist [Senior] }", "company_name": "Say \\"hi\\" {"}]}'
    assert _extract_jobs(text) == [{"job_title": "Artist [Senior] }", "company_name": 'Say "hi" {'}]


def test_failed_response_raises():
    import asyncio
    from types import SimpleNamespace

    from bot.openai_client import OpenAIRequestError

    for code, retryable in (("server_error", True), ("invalid_image_url", False)):
        response = SimpleNamespace(status="failed", error=SimpleNamespace(code=code), output_text="")
        parser, _ = _parser_with_fake_client(response=response)

        with pytest.raises(OpenAIRequestError) as excinfo:
            asyncio.run(parser.parse_from_text(content="Producer wanted", url="https://example.com"))

        assert excinfo.value.error_type == "ResponseFailed"
        assert excinfo.value.retryable is retryable


def test_config_leaves_temperature_unset_without_env(monkeypatch):
    from bot.config import load_config

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord")
    monkeypatch.setenv("OPENAI_API_KEY", "openai")
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    config = load_config()

    assert config.openai.temperature is None
    assert config.openai.model == "gpt-6.1-terra"


def test_config_reads_jobbot_limits(monkeypatch):
    from bot.config import load_config

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord")
    monkeypatch.setenv("OPENAI_API_KEY", "openai")
    monkeypatch.setenv("JOB_TEAM_CHANNEL_IDS", "art:1,game_design:2,dev:3,others:4")
    monkeypatch.setenv("JOBBOT_TRUSTED_USER_IDS", " 111, 222 ,")
    monkeypatch.setenv("JOBBOT_MAX_REQUESTS_PER_HOUR", "7")
    monkeypatch.delenv("JOBBOT_MAX_REQUESTS_PER_DAY", raising=False)

    config = load_config()

    assert config.trusted_user_ids == frozenset({111, 222})
    assert config.max_requests_per_hour == 7
    assert config.max_requests_per_day == 100


def test_config_rejects_invalid_trusted_ids(monkeypatch):
    from bot.config import load_config

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord")
    monkeypatch.setenv("OPENAI_API_KEY", "openai")
    monkeypatch.setenv("JOB_TEAM_CHANNEL_IDS", "art:1,game_design:2,dev:3,others:4")
    monkeypatch.setenv("JOBBOT_TRUSTED_USER_IDS", "123,abc")

    with pytest.raises(RuntimeError, match="JOBBOT_TRUSTED_USER_IDS"):
        load_config()


# --- Links section ------------------------------------------------------------------


def test_links_section_puts_job_looking_links_first():
    from bot.openai_client import build_links_section

    links = [
        "https://www.studio.example.com/about",
        "https://www.studio.example.com/blog/post",
        "https://jobs.lever.co/studio/42",
        "https://studio.example.com/careers/senior-artist",
        "https://studio.example.com/contact",
        "https://studio.example.com/en/open-positions/7",
        "https://studio.example.com/apply?id=3",
        "https://studio.example.com/team/vacancies",
    ]

    section = build_links_section(links, "https://www.studio.example.com/careers")

    listed = [line[2:] for line in section.splitlines() if line.startswith("- ")]
    assert listed == [
        "https://jobs.lever.co/studio/42",
        "https://studio.example.com/careers/senior-artist",
        "https://studio.example.com/en/open-positions/7",
        "https://studio.example.com/apply?id=3",
        "https://studio.example.com/team/vacancies",
        "https://www.studio.example.com/about",
        "https://www.studio.example.com/blog/post",
        "https://studio.example.com/contact",
    ]
    assert "exact link from this list" in section
    assert "omit job_url" in section


def test_links_section_filters_dedupes_and_caps():
    from bot.openai_client import (
        MAX_LINK_CHARS,
        MAX_LINKS_SECTION_CHARS,
        MAX_PROMPT_LINKS,
        build_links_section,
    )

    too_long = "https://studio.example.com/jobs/" + "x" * MAX_LINK_CHARS
    links = (
        ["mailto:jobs@studio.example.com", "ftp://studio.example.com/jobs", too_long, "https://studio.example.com/jobs/1"]
        + ["https://studio.example.com/jobs/1"] * 3
        + [f"https://studio.example.com/jobs/{index}" for index in range(2, 200)]
    )

    section = build_links_section(links, "https://studio.example.com/careers")
    listed = [line[2:] for line in section.splitlines() if line.startswith("- ")]

    assert len(listed) == MAX_PROMPT_LINKS
    assert listed[0] == "https://studio.example.com/jobs/1"
    assert len(set(listed)) == len(listed)
    assert all(link.startswith("https://") and len(link) <= MAX_LINK_CHARS for link in listed)
    assert too_long not in section
    assert len(section) <= MAX_LINKS_SECTION_CHARS


def test_links_section_respects_total_size():
    from bot.openai_client import MAX_LINKS_SECTION_CHARS, MAX_PROMPT_LINKS, build_links_section

    # 60 links of ~190 chars (~11.5K) cannot all fit in the section.
    links = [f"https://jobs.example.org/{index:03d}/" + "a" * 160 for index in range(MAX_PROMPT_LINKS)]

    section = build_links_section(links, "https://studio.example.com/careers")
    listed = [line for line in section.splitlines() if line.startswith("- ")]

    assert len(section) <= MAX_LINKS_SECTION_CHARS
    assert 0 < len(listed) < MAX_PROMPT_LINKS
    assert listed[0].startswith("- https://jobs.example.org/000/")


def test_links_section_is_empty_without_usable_links():
    from bot.openai_client import build_links_section

    assert build_links_section([], "https://studio.example.com/careers") == ""
    assert build_links_section(["mailto:a@b.c", "javascript:void(0)"], "https://studio.example.com") == ""


def test_text_prompt_is_unchanged_without_links():
    import asyncio

    from bot.openai_client import MAX_PROMPT_CHARS, TEXT_PROMPT_TEMPLATE

    parser, responses = _parser_with_fake_client()
    content = "Producer wanted " * 3000

    asyncio.run(parser.parse_from_text(content=content, url="https://example.com"))
    asyncio.run(parser.parse_from_text(content=content, url="https://example.com", links=()))

    expected = TEXT_PROMPT_TEMPLATE.format(url="https://example.com", content=content[:MAX_PROMPT_CHARS])
    assert [call["input"][1]["content"] for call in responses.calls] == [expected, expected]
    assert "Links found on the page" not in expected


def test_text_prompt_lists_links_after_the_capped_page_text():
    import asyncio

    from bot.openai_client import MAX_LINKS_SECTION_CHARS, MAX_PROMPT_CHARS, TEXT_PROMPT_TEMPLATE

    parser, responses = _parser_with_fake_client()
    content = "x" * (MAX_PROMPT_CHARS + 5000)

    asyncio.run(
        parser.parse_from_text(
            content=content,
            url="https://studio.example.com/careers",
            links=["https://studio.example.com/about", "https://jobs.lever.co/studio/42"],
        )
    )

    prompt = responses.calls[0]["input"][1]["content"]
    base = TEXT_PROMPT_TEMPLATE.format(url="https://studio.example.com/careers", content=content[:MAX_PROMPT_CHARS])
    assert prompt.startswith(base)
    section = prompt[len(base):]
    assert section.index("https://jobs.lever.co/studio/42") < section.index("https://studio.example.com/about")
    assert len(prompt) <= len(base) + MAX_LINKS_SECTION_CHARS
