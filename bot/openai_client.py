from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlsplit

import openai
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
# Links appended after the page text so the model can copy each posting's exact URL.
MAX_PROMPT_LINKS = 60
MAX_LINK_CHARS = 200
MAX_LINKS_SECTION_CHARS = 9000
JOB_LINK_HINTS = ("job", "career", "position", "apply", "opening", "vacanc")
LINKS_SECTION_HEADER = (
    "\nLinks found on the page, each followed by its link text when it has one. For each job, "
    "set job_url to the exact link from this list whose text or address shows that it is that "
    "specific posting. Never give the same job_url to two different jobs, and never use links to "
    "other offers (similar or recommended jobs), social profiles or generic pages: "
    "omit job_url if no link clearly matches.\n"
)
LINK_LABEL_SEPARATOR = " | link text: "
MAX_OUTPUT_TOKENS = 16000
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
    """The OpenAI call itself failed, as opposed to returning output that could not be parsed.

    ``retryable`` is True only for transient failures (connection, timeout, rate limit, 5xx):
    those keep the /jobbot request queued. Anything else (4xx such as a bad image URL)
    is a per-URL failure.
    """

    def __init__(self, error_type: str, message: str = "", *, retryable: bool = False) -> None:
        super().__init__(message or error_type)
        self.error_type = error_type
        self.retryable = retryable


class OpenAITruncatedError(OpenAIRequestError):
    """The response stopped early (status "incomplete"): its JSON must not be used."""


# Response.error codes worth retrying when status is "failed".
RETRYABLE_RESPONSE_ERROR_CODES = frozenset({"server_error", "rate_limit_exceeded"})


def _is_retryable(exc: BaseException) -> bool:
    # APITimeoutError is a subclass of APIConnectionError.
    if isinstance(exc, (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code >= 500


class OpenAIJobParser:
    def __init__(self, config: OpenAIConfig, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._client = OpenAI(api_key=config.api_key, timeout=timeout, max_retries=1)
        self._text_model = config.model
        self._image_model = config.image_model or config.model
        self._config = config

    async def parse_from_text(
        self,
        *,
        content: str,
        url: str,
        links: Iterable[str] = (),
        link_labels: Optional[Mapping[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        if not content:
            return []
        prompt = TEXT_PROMPT_TEMPLATE.format(url=url, content=content[:MAX_PROMPT_CHARS])
        prompt += build_links_section(links, url, link_labels)
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
            # Reasoning models spend part of this budget before writing the JSON; long
            # career pages need room for many jobs.
            "max_output_tokens": MAX_OUTPUT_TOKENS,
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
            retryable = _is_retryable(exc)
            logger.error("🚫 OpenAI request failed (%s, retryable=%s): %s", error_type, retryable, exc)
            raise OpenAIRequestError(error_type, str(exc), retryable=retryable) from exc

        status = getattr(response, "status", None)
        if status == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None) or "incomplete"
            logger.warning("⚠️ OpenAI response incomplete, discarding it: %s", details)
            raise OpenAITruncatedError(str(reason), f"OpenAI response incomplete: {details}")
        if status in {"failed", "cancelled"}:
            error = getattr(response, "error", None)
            code = getattr(error, "code", None)
            logger.error("🚫 OpenAI response %s: %s", status, error)
            raise OpenAIRequestError(
                f"Response{status.capitalize()}",
                str(error or status),
                retryable=code in RETRYABLE_RESPONSE_ERROR_CODES,
            )

        raw_text = getattr(response, "output_text", "") or ""
        logger.info("🤖 OpenAI responded with %s characters", len(raw_text))
        return raw_text


def _site_host(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _looks_like_job_link(link: str, source_host: str) -> bool:
    if _site_host(link) != source_host:
        return True
    path = urlsplit(link).path.lower()
    return any(hint in path for hint in JOB_LINK_HINTS)


def build_links_section(
    links: Iterable[str],
    source_url: str,
    labels: Optional[Mapping[str, str]] = None,
) -> str:
    """List the page's links for the model, job-looking ones first; "" when there are none.

    Keeps http(s) links of at most MAX_LINK_CHARS (longer ones are skipped rather than cut,
    since a cut URL is useless), deduplicated, at most MAX_PROMPT_LINKS of them and
    MAX_LINKS_SECTION_CHARS in total. Job-looking links (another host than the source, or a
    path mentioning jobs/careers/...) come first; the rest fill up in page order. A link's
    text from ``labels`` follows it on its line, so the model can match links to jobs.
    """
    labels = labels or {}
    source_host = _site_host(source_url)
    candidates: List[str] = []
    seen = set()
    for link in links:
        if not isinstance(link, str):
            continue
        link = link.strip()
        if not link or link in seen or len(link) > MAX_LINK_CHARS:
            continue
        if not link.lower().startswith(("http://", "https://")) or not _site_host(link):
            continue
        seen.add(link)
        candidates.append(link)

    preferred = [link for link in candidates if _looks_like_job_link(link, source_host)]
    others = [link for link in candidates if not _looks_like_job_link(link, source_host)]

    lines: List[str] = []
    size = len(LINKS_SECTION_HEADER)
    for link in preferred + others:
        label = labels.get(link)
        label = " ".join(label.split()) if isinstance(label, str) else ""
        line = f"- {link}{LINK_LABEL_SEPARATOR}{label}\n" if label else f"- {link}\n"
        if len(lines) >= MAX_PROMPT_LINKS or size + len(line) > MAX_LINKS_SECTION_CHARS:
            break
        lines.append(line)
        size += len(line)
    if not lines:
        return ""
    return LINKS_SECTION_HEADER + "".join(lines)


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


def _top_level_starts(text: str) -> Iterable[int]:
    """Yield the index of every '[' or '{' that is not nested inside another bracket.

    Nesting follows JSON strings (with escapes) inside brackets; quotes in surrounding
    prose are ignored. A structure that is opened but never closed (cut-off output) keeps
    everything after it nested, so its inner objects are never offered as candidates.
    """
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char in "[{":
            if depth == 0:
                yield index
            depth += 1
        elif char in "]}":
            depth = max(depth - 1, 0)
        elif char == '"' and depth > 0:
            in_string = True


def _decode_candidates(text: str) -> Iterable[Any]:
    """Yield the JSON values in ``text``: the whole text, else each top-level structure."""
    try:
        yield json.loads(text)
        return
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index in _top_level_starts(text):
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
