from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List

from openai import OpenAI

from .config import OpenAIConfig
from .utils import run_blocking

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You extract structured summaries from video game industry job postings. "
    'Always answer with a single JSON object of the form {"jobs": [...]}, where "jobs" is an array '
    "with one object per distinct job posting (an empty array when there is none). Each job object can contain: "
    "job_title, company_name, job_url (absolute URL of that specific posting, only when the source gives one), "
    "source_url, location, work_model (Remote, Hybrid, Onsite), "
    "seniority (Junior, Mid, Senior, Lead, Director), contract_type, remote_friendly (boolean), compensation, "
    "description_summary, skills (array of short phrases), team (art, game_design, dev, others), known_titles (array). "
    "Omit keys when information is unavailable. Keep values concise and human friendly."
)

TEXT_PROMPT_TEMPLATE = (
    "Parse every distinct job posting present in the following page copy. "
    'Return the JSON object {{"jobs": [...]}} with one dictionary per job.'
    "\nSource URL: {url}\n" + "-" * 40 + "\n{content}\n"
)

IMAGE_PROMPT_TEMPLATE = (
    "Parse every distinct job posting present in this image capture. "
    'Return the JSON object {{"jobs": [...]}}; if multiple jobs exist, include them all in the "jobs" array.'
    "\nSource Reference: {url}"
)

MAX_PROMPT_CHARS = 30000
DEFAULT_TIMEOUT = 60.0

JOB_KEYS = frozenset(
    {
        "job_title",
        "company_name",
        "job_url",
        "source_url",
        "location",
        "work_model",
        "seniority",
        "contract_type",
        "remote_friendly",
        "compensation",
        "description_summary",
        "skills",
        "team",
        "known_titles",
    }
)


class OpenAIRequestError(RuntimeError):
    """The OpenAI call itself failed (network, timeout, API error), as opposed to unparseable output."""

    def __init__(self, error_type: str, message: str = "") -> None:
        super().__init__(message or error_type)
        self.error_type = error_type


class OpenAIJobParser:
    def __init__(self, config: OpenAIConfig, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._client = OpenAI(api_key=config.api_key, timeout=timeout, max_retries=1)
        self._text_model = config.model
        self._image_model = config.image_model or config.model
        self._config = config

    async def parse_from_text(self, *, content: str, url: str) -> List[Dict[str, Any]]:
        if not content:
            return []
        prompt = TEXT_PROMPT_TEMPLATE.format(url=url, content=content[:MAX_PROMPT_CHARS])
        messages = _build_text_messages(prompt)
        raw_text = await self._call_openai(messages, model=self._text_model)
        return _extract_jobs(raw_text)

    async def parse_from_image(self, *, image_url: str, url: str) -> List[Dict[str, Any]]:
        if not image_url:
            return []
        prompt = IMAGE_PROMPT_TEMPLATE.format(url=url)
        messages = _build_image_messages(prompt, image_url)
        raw_text = await self._call_openai(messages, model=self._image_model)
        return _extract_jobs(raw_text)

    async def _call_openai(self, messages: Iterable[Dict[str, Any]], *, model: str) -> str:
        """Return the model's text output; raise OpenAIRequestError when the request fails."""
        request: Dict[str, Any] = {
            "model": model,
            "input": list(messages),
            # Reasoning models spend part of this budget before writing the JSON.
            "max_output_tokens": 4000,
            "text": {"format": {"type": "json_object"}},
        }
        if self._config.temperature is not None:
            request["temperature"] = self._config.temperature

        def _send_request() -> Any:
            return self._client.responses.create(**request)

        try:
            response = await run_blocking(_send_request)
        except Exception as exc:  # noqa: BLE001
            error_type = type(exc).__name__
            logger.error("🚫 OpenAI request failed (%s): %s", error_type, exc)
            raise OpenAIRequestError(error_type, str(exc)) from exc

        status = getattr(response, "status", None)
        if status == "incomplete":
            logger.warning(
                "⚠️ OpenAI response incomplete: %s",
                getattr(response, "incomplete_details", None),
            )
        elif status in {"failed", "cancelled"}:
            error = getattr(response, "error", None)
            logger.error("🚫 OpenAI response %s: %s", status, error)
            raise OpenAIRequestError(f"Response{status.capitalize()}", str(error or status))

        raw_text = getattr(response, "output_text", "") or ""
        logger.info("🤖 OpenAI responded with %s characters", len(raw_text))
        return raw_text


def _build_text_messages(prompt: str) -> List[Dict[str, Any]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def _build_image_messages(prompt: str, image_url: str) -> List[Dict[str, Any]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": image_url},
            ],
        },
    ]


_NOT_JOBS = object()


def _jobs_from_payload(data: Any) -> Any:
    """Return the job dicts held by a decoded payload, or _NOT_JOBS if it holds none."""
    if isinstance(data, dict):
        if "jobs" in data:
            jobs = data["jobs"]
            if isinstance(jobs, dict):
                return [jobs]
            if isinstance(jobs, list):
                return [item for item in jobs if isinstance(item, dict)]
            return _NOT_JOBS
        if JOB_KEYS.intersection(data):
            return [data]
        return _NOT_JOBS
    if isinstance(data, list):
        jobs = [item for item in data if isinstance(item, dict)]
        if data and not jobs:
            return _NOT_JOBS
        return jobs
    return _NOT_JOBS


def _decode_candidates(text: str) -> Iterable[Any]:
    """Yield JSON values found in ``text``: the whole text first, then from each '[' or '{'."""
    try:
        yield json.loads(text)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        yield value


def _extract_jobs(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))
        text = text.strip()

    first_value: Any = _NOT_JOBS
    reported_empty = False
    for value in _decode_candidates(text):
        if first_value is _NOT_JOBS:
            first_value = value
        jobs = _jobs_from_payload(value)
        if jobs is _NOT_JOBS:
            continue
        if jobs:
            return jobs
        reported_empty = True

    preview = text[:200].replace("\n", " ")
    if reported_empty:
        logger.info("📭 OpenAI reported no job postings")
    elif first_value is _NOT_JOBS:
        logger.warning("⚠️ No JSON payload detected in OpenAI response (preview: %s...)", preview)
    elif isinstance(first_value, list):
        logger.warning("⚠️ JSON array contained no dict objects (preview: %s...)", preview)
    else:
        logger.warning("⚠️ OpenAI JSON did not contain job objects (preview: %s...)", preview)
    return []
