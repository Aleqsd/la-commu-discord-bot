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
    "Always return a JSON array of job objects. Each job object can contain: "
    "job_title, company_name, job_url, source_url, location, work_model (Remote, Hybrid, Onsite), "
    "seniority (Junior, Mid, Senior, Lead, Director), contract_type, remote_friendly (boolean), compensation, "
    "description_summary, skills (array of short phrases), team (art, game_design, dev, others), known_titles (array). "
    "Omit keys when information is unavailable. Keep values concise and human friendly."
)

TEXT_PROMPT_TEMPLATE = (
    "Parse every distinct job posting present in the following page copy. "
    "Return one JSON array with a dictionary per job."
    "\nSource URL: {url}\n" + "-" * 40 + "\n{content}\n"
)

IMAGE_PROMPT_TEMPLATE = (
    "Parse every distinct job posting present in this image capture. "
    "If multiple jobs exist, include them all in the JSON array."
    "\nSource Reference: {url}"
)

MAX_PROMPT_CHARS = 6000


class OpenAIJobParser:
    def __init__(self, config: OpenAIConfig) -> None:
        self._client = OpenAI(api_key=config.api_key)
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
        def _send_request() -> str:
            response = self._client.responses.create(
                model=model,
                temperature=self._config.temperature,
                input=list(messages),
                max_output_tokens=1200,
            )
            return response.output_text

        try:
            raw_text = await run_blocking(_send_request)
            logger.info("🤖 OpenAI responded with %s characters", len(raw_text))
            return raw_text
        except Exception as exc:  # noqa: BLE001
            logger.error("🚫 OpenAI request failed: %s", exc)
            return ""


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
                {"type": "input_image", "image_url": {"url": image_url}},
            ],
        },
    ]


def _extract_jobs(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))
        text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        # Fallback to single object parsing
        obj_start = text.find("{")
        obj_end = text.rfind("}")
        if obj_start == -1 or obj_end == -1:
            preview = text[:200].replace("\n", " ")
            logger.warning("⚠️ No JSON payload detected in OpenAI response (preview: %s...)", preview)
            return []
        json_str = text[obj_start : obj_end + 1]
    else:
        json_str = text[start : end + 1]

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.warning(
            "⚠️ Failed to parse OpenAI JSON: %s | snippet=%s",
            exc,
            json_str[:200].replace("\n", " "),
        )
        return []

    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        jobs = [item for item in data if isinstance(item, dict)]
        if not jobs:
            logger.warning("⚠️ JSON array contained no dict objects")
        return jobs
    logger.warning("⚠️ Unexpected JSON type: %s", type(data))
    return []
